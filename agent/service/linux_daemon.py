"""Linux agent daemon: signal-based lifecycle with systemd support.

Supports four modes:
- Default (no args): run as a foreground daemon (suitable for systemd or terminal)
- ``gui``: launch the tkinter GUI
- ``install``: create and enable a systemd unit file
- ``remove``: disable and remove the systemd unit file
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from agent.core.agent_runtime import AgentHooks, AgentRuntime
from shared.utils import setup_file_logging, setup_logging

_SYSTEMD_TASK_NAME = "agent"


def _get_base_dir() -> Path:
    """Get base directory for development and frozen execution."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


async def _run_daemon() -> None:
    """Run the agent as a foreground daemon with signal handling."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # systemd sends SIGTERM on stop; Ctrl+C sends SIGINT in terminal
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    hooks = AgentHooks(is_service_mode=True)
    runtime = AgentRuntime(
        base_dir=_get_base_dir(),
        stop_event=stop_event,
        hooks=hooks,
    )
    await runtime.run()


def _install_systemd() -> None:
    """Register agent as a systemd service."""
    from agent.platform import get_platform

    platform = get_platform()
    base_dir = _get_base_dir()

    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        args = None
    else:
        exe = Path(sys.executable)  # python interpreter
        args = ["-m", "agent"]

    success = platform.register_autostart(
        task_name=_SYSTEMD_TASK_NAME,
        exe=exe,
        args=args,
        cwd=str(base_dir),
    )

    if success:
        print(f"systemd service registered. Base dir: {base_dir}")
        print("Start with: systemctl start ailogops-agent.service")
        print("Enable auto-start: systemctl enable ailogops-agent.service")
    else:
        print("Failed to register systemd service.", file=sys.stderr)
        sys.exit(1)


def _remove_systemd() -> None:
    """Unregister the systemd service."""
    from agent.platform import get_platform

    platform = get_platform()
    success = platform.unregister_autostart(_SYSTEMD_TASK_NAME)

    if success:
        print("systemd service removed.")
    else:
        print("Failed to remove systemd service.", file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    """Linux agent entry point."""
    args = sys.argv if argv is None else argv
    logger = setup_logging("linux_daemon")

    if len(args) > 1:
        cmd = args[1].lower()

        if cmd == "gui":
            from agent.gui.app import run_gui

            run_gui()
            return

        if cmd == "install":
            _install_systemd()
            return

        if cmd == "remove":
            _remove_systemd()
            return

        logger.warning("unknown command: %s (expected: gui, install, remove)", cmd)
        print(f"Usage: {args[0]} [gui|install|remove]", file=sys.stderr)
        sys.exit(1)

    # Default: daemon mode
    setup_file_logging(_get_base_dir() / "log")
    logger.info("starting agent daemon (Linux)")
    asyncio.run(_run_daemon())


if __name__ == "__main__":
    main()
