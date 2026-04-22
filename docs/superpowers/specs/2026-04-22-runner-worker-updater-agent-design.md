# Runner Worker Updater Agent Design

## Background

The current agent process owns all of the following at once:

- server TCP control channel
- log collection and analysis coordination
- process control and deploy execution
- file management commands
- self-update and restart flow

This coupling makes agent deployment fragile. A failed update or restart can
take down the same process that the server relies on for remote control. Recent
field issues showed exactly that pattern: deploy succeeded at the transport
layer, but the agent did not reliably reconnect after update.

The highest-priority requirement is therefore:

- the remote control channel must remain available even when worker tasks fail
  or the worker is being updated

Secondary failures such as log-processing failure or process-control failure are
acceptable as long as they can be retried, restarted, or recovered through a
worker update.

## Goals

- Keep a stable remote control channel alive independently from worker runtime
  failures.
- Make worker update, restart, and rollback possible without taking the agent
  fully offline.
- Reduce blast radius so a failure in log/process/recording features does not
  kill the control plane.
- Preserve current server-side operator model: one logical agent per site.

## Non-Goals

- Do not redesign the external server protocol end-to-end in phase 1.
- Do not make Runner hot-updatable through the normal worker deployment path.
- Do not split the system into many independently networked services.
- Do not refactor unrelated business logic while performing the split.

## Proposed Architecture

The agent is split into three executables with asymmetric trust and lifecycle:

- Runner
  - fixed, minimal, long-lived control-plane process
  - owns server TCP session, authentication, heartbeat, status reporting
  - owns local state machine for worker lifecycle and update orchestration
  - starts, stops, monitors, and restarts Worker
  - invokes Updater only when needed
- Worker
  - replaceable data-plane process
  - owns operational features such as log watchers, process control, file
    operations, recording, analysis, and deploy work
  - does not own the server session
  - communicates only with Runner over local IPC
- Updater
  - replaceable but not permanently running
  - launched on demand by Runner
  - validates, stages, swaps, and rolls back Worker packages
  - exits after completion

The server always talks to Runner. Runner always remains the identity of the
site-level agent. Worker becomes an internal component, not the network-facing
agent.

## Responsibility Boundaries

### Runner

Runner responsibilities are intentionally narrow:

- connect and authenticate to server
- maintain heartbeat and logical agent online state
- receive remote commands and route them locally
- receive uploaded packages and place them into staging
- start, stop, and monitor Worker
- start Updater and validate update outcomes
- report structured status to server:
  - runner status
  - worker status
  - worker version
  - degraded/update/rollback state
- persist last known good worker version and lifecycle state

Runner must not perform heavy operational work such as log scanning, file
enumeration, process deploy execution, or recording analysis.

### Worker

Worker owns almost all current `AgentRuntime` feature work:

- realtime log watch and historical log collection
- process managers and process deploy handlers
- recording control and upload workflows
- config/file/exec request execution
- site-specific automation and recovery logic

Worker is disposable. If Worker crashes, hangs, or fails update, Runner should
restart or replace it without losing the remote channel.

### Updater

Updater is a single-purpose executable:

- validate package structure and metadata
- unpack to staging
- stop Worker
- install target worker version into versioned directory
- switch active pointer/directory
- request Worker start via Runner
- restore previous worker version on failed validation/startup

Updater must not own the TCP session and must not be required for normal agent
operation outside update windows.

## Process and Package Layout

Recommended install layout:

```text
install/
  runner/
    AILogOps-Runner.exe
    runner.yaml
    state.json
  worker/
    current/
      ...
    versions/
      1.11.1/
        ...
      1.11.2/
        ...
  updater/
    AILogOps-Updater.exe
  staging/
    incoming/
    unpacked/
  logs/
    runner.log
    worker.log
    updater.log
```

Key rule:

- Runner and Worker must not share the same mutable install directory.

This removes self-overwrite problems, file lock ambiguity, partial-copy races,
and "replace the process that is still controlling the replacement" failures.

## IPC Design

Runner and Worker communicate over loopback-only local IPC. The initial design
uses one Runner-owned local control port on the same host.

Message types are intentionally small:

- `register`
- `heartbeat`
- `command`
- `result`
- `shutdown`
- `upgrade_prepare`

Runner is the state owner. Worker is the task executor. Runner may reject,
queue, retry, or cancel commands depending on worker health.

Why this approach:

- simpler than reusing the full server protocol internally
- easy to inspect in logs
- process boundary remains explicit
- future replacement with named pipe is possible without changing the high-level
  protocol model

## Status Model

Server-visible agent status must be derived from Runner, not Worker.

Suggested state model:

- `ONLINE`
  - Runner connected and Worker healthy
- `DEGRADED`
  - Runner connected but Worker unavailable, restarting, or unhealthy
- `UPDATING`
  - Runner connected, update in progress, Worker intentionally transitioning
- `ROLLBACK`
  - Runner connected, previous Worker version being restored
- `OFFLINE`
  - Runner unavailable

This preserves operator control during Worker failures. "Agent offline" is only
true when Runner itself is down.

## Normal Startup Flow

1. Service manager starts Runner.
2. Runner connects to server and authenticates.
3. Runner reports control-plane readiness even if Worker is not running yet.
4. Runner reads `state.json` to identify:
   - current worker version
   - last known good version
   - incomplete update state
5. Runner starts Worker from `worker/current/`.
6. Worker registers with Runner over local IPC.
7. Runner marks Worker healthy after registration and heartbeat.
8. Runner begins routing operational commands to Worker.

## Worker Failure Flow

1. Runner misses Worker heartbeat or sees process exit.
2. Runner marks site `DEGRADED`.
3. Runner attempts restart up to a configured retry threshold.
4. If restart succeeds, Runner returns status to `ONLINE`.
5. If repeated restart fails, Runner remains connected and reports Worker
   unavailable.

This is the core value of the split: remote control survives repeated Worker
failure.

## Update Flow

1. Server uploads Worker package to Runner.
2. Runner stores it under `staging/incoming/`.
3. Runner validates request metadata and target component type.
4. Runner launches Updater.
5. Updater unpacks the package to `staging/unpacked/`.
6. Updater requests Worker stop through Runner.
7. Updater installs the new Worker into `worker/versions/<version>/`.
8. Runner switches `worker/current/` to the new version target.
9. Runner starts the new Worker.
10. Runner waits for Worker registration and heartbeat.
11. If healthy, Runner marks the version as current and last known good.
12. If not healthy, Runner triggers rollback to the previous good version.

Critical rule:

- Runner itself is never part of the normal Worker update transaction.

## Rollback Rules

Rollback is automatic when:

- Worker fails to start
- Worker registers but fails health verification
- Worker exits repeatedly within a short post-update window
- package validation fails after stop decision but before healthy activation

Rollback source:

- last known good Worker version recorded in `state.json`

Rollback result:

- Runner returns to `ONLINE` if old Worker comes back
- otherwise remains `DEGRADED` but still connected

## State Persistence

Runner stores durable local state in `runner/state.json`.

Required fields:

- `current_worker_version`
- `last_good_worker_version`
- `desired_worker_version`
- `worker_status`
- `update_in_progress`
- `rollback_in_progress`
- `restart_fail_count`
- `last_update_started_at`
- `last_update_result`

This file allows Runner to recover correctly after host reboot or Runner
restart, including incomplete update cleanup.

## Deployment Model

The system has three separate deployment classes:

- Runner deployment
  - rare
  - manual or special maintenance workflow
  - never piggyback on ordinary site worker rollout
- Worker deployment
  - normal operational rollout path
  - handled by Runner and Updater
- Updater deployment
  - maintained together with Runner-level trusted components
  - updated only through controlled workflow

Operationally, "agent deploy" should become "worker deploy" in most cases.

## Server and Dashboard Changes

Server continues to treat each site as a single logical agent, but dashboard and
API payloads should expose sub-status fields:

- `runner_version`
- `worker_version`
- `runner_status`
- `worker_status`
- `degraded_reason`
- `update_phase`

The server must stop assuming that a restart or update necessarily disconnects
the site. A temporary Worker outage should remain operator-visible but
recoverable.

## Migration Plan

### Phase 1: Introduce Runner shell

- Add Runner executable as the real service entry point.
- Keep current runtime mostly intact inside a Worker-like subprocess.
- Runner owns server TCP and lifecycle reporting.

### Phase 2: Move current runtime into Worker

- Extract current `AgentRuntime` operational logic into Worker runtime.
- Replace direct server usage in Worker with Runner IPC calls where needed.

### Phase 3: Split Updater

- Convert current self-update logic into Worker-only package swap flow.
- Ensure Runner is excluded from normal update payloads.

### Phase 4: Add durable state machine

- Introduce `state.json`
- add startup recovery for interrupted update/rollback
- add retry policy and degraded reporting

### Phase 5: Dashboard and protocol enrichment

- expose Runner/Worker split status
- refine deployment commands to explicitly target Worker

## Risks and Trade-Offs

- IPC adds complexity and a new local protocol surface.
- Packaging becomes more complex because one logical agent is now multiple
  binaries.
- Debugging requires correlating three logs instead of one.
- Runner stability becomes critical and must be treated like a small supervisor,
  not a feature container.

These costs are acceptable because they directly buy the top requirement:
survival of the remote control channel during worker failure and worker update.

## Alternatives Considered

### A. Keep current monolith and only externalize self-update

Rejected because the control plane still lives inside the same large runtime
that performs feature work. Partial improvement only.

### B. Split all three into fully independent networked services

Rejected for phase 1 because operational and protocol complexity is too high for
the current codebase and rollout risk.

### C. Fixed Runner plus replaceable Worker plus on-demand Updater

Accepted because it is the smallest architecture that preserves the control
plane under update and failure.

## Verification Strategy

The new design should be considered acceptable only if it passes these checks:

- killing Worker does not disconnect the site from server
- Worker crash loop still leaves Runner controllable
- Worker update success changes only Worker version, not Runner connectivity
- Worker update failure rolls back without site going offline
- interrupted update followed by host reboot recovers cleanly via `state.json`
- dashboard clearly distinguishes `DEGRADED` from `OFFLINE`

## Recommendation

Proceed with a phased implementation of:

- fixed Runner as the always-on control plane
- replaceable Worker for operational tasks
- on-demand Updater for Worker package swap and rollback

This directly addresses the observed deployment instability while keeping the
external operational model close to the current system.
