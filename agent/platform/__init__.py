"""Platform abstraction layer for cross-platform agent support."""

from __future__ import annotations

import sys

from agent.platform.base import PlatformHelper


def get_platform() -> PlatformHelper:
    """Return platform-specific helper for the current OS."""
    if sys.platform == "win32":
        from agent.platform.windows import WindowsPlatform

        return WindowsPlatform()
    elif sys.platform == "darwin":
        from agent.platform.darwin import DarwinPlatform

        return DarwinPlatform()
    else:
        raise NotImplementedError(f"Unsupported platform: {sys.platform}")
