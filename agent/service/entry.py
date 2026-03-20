"""Cross-platform agent entry point.

Routes to the appropriate platform-specific service/daemon implementation.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    """Unified entry point for all platforms."""
    args = sys.argv if argv is None else argv

    if sys.platform == "win32":
        from agent.service.win_service import main as win_main

        win_main(args)
    elif sys.platform == "darwin":
        from agent.service.mac_daemon import main as mac_main

        mac_main(args)
    else:
        raise NotImplementedError(f"Unsupported platform: {sys.platform}")


if __name__ == "__main__":
    main()
