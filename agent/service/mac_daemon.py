"""macOS agent daemon: signal-based lifecycle with launchd support.

Supports three modes:
- Default (no args): run as a foreground daemon (suitable for launchd or terminal)
- ``gui``: launch the tkinter GUI
- ``install``: register a launchd plist for auto-start
- ``remove``: unregister the launchd plist
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from agent.core.agent_runtime import AgentHooks, AgentRuntime
from shared.utils import setup_file_logging, setup_logging

_LAUNCHD_TASK_NAME = "agent"


def _get_base_dir() -> Path:
    """Get base directory for development and frozen execution."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


async def _run_daemon() -> None:
    """Run the agent as a foreground daemon with signal handling."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # launchd sends SIGTERM on unload; Ctrl+C sends SIGINT in terminal
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    hooks = AgentHooks(is_service_mode=True)
    runtime = AgentRuntime(
        base_dir=_get_base_dir(),
        stop_event=stop_event,
        hooks=hooks,
    )
    await runtime.run()


def _install_launchd() -> None:
    """Register agent as a launchd user agent."""
    from agent.platform import get_platform

    platform = get_platform()
    base_dir = _get_base_dir()

    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
    else:
        exe = Path(sys.executable)  # python interpreter
        # For development, we'd want to run the module
        # This will be adjusted for frozen builds

    success = platform.register_autostart(
        task_name=_LAUNCHD_TASK_NAME,
        exe=exe,
        args=None,
        cwd=str(base_dir),
    )

    if success:
        print(f"launchd agent registered. Base dir: {base_dir}")
        print("The agent will start automatically on login.")
    else:
        print("Failed to register launchd agent.", file=sys.stderr)
        sys.exit(1)


def _remove_launchd() -> None:
    """Unregister the launchd user agent."""
    from agent.platform import get_platform

    platform = get_platform()
    success = platform.unregister_autostart(_LAUNCHD_TASK_NAME)

    if success:
        print("launchd agent removed.")
    else:
        print("Failed to remove launchd agent.", file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    """macOS agent entry point."""
    args = sys.argv if argv is None else argv
    logger = setup_logging("mac_daemon")

    if len(args) > 1:
        cmd = args[1].lower()

        if cmd == "gui":
            from agent.gui.app import run_gui

            run_gui()
            return

        if cmd == "install":
            _install_launchd()
            return

        if cmd == "remove":
            _remove_launchd()
            return

        logger.warning("unknown command: %s (expected: gui, install, remove)", cmd)
        print(f"Usage: {args[0]} [gui|install|remove]", file=sys.stderr)
        sys.exit(1)

    # Default: daemon mode
    setup_file_logging(_get_base_dir() / "log")
    logger.info("starting agent daemon (macOS)")
    asyncio.run(_run_daemon())


if __name__ == "__main__":
    main()
