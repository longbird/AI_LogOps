"""Typed configuration accessor for dict/Mapping-based configs.

Provides a clean, type-safe interface for accessing nested configuration
with sensible defaults and automatic type conversion.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast


class ConfigView:
    """Typed accessor for configuration dictionaries.

    Wraps a raw dict or Mapping and provides typed access methods with
    automatic conversion and sensible defaults.

    Example:
        cfg = ConfigView({"agent": {"id": "my-agent", "port": 9500}})
        agent_cfg = cfg.sub("agent")
        agent_id = agent_cfg.s("id", "unknown")  # "my-agent"
        port = agent_cfg.i("port", 8000)  # 9500
    """

    __slots__: tuple[str, ...] = ("_data",)

    def __init__(self, data: object = None) -> None:
        """Initialize ConfigView with raw data.

        Args:
            data: A dict, Mapping, or any value. Non-Mapping values
                  (including None) are treated as empty dict.
        """
        if isinstance(data, Mapping):
            self._data: Mapping[str, object] = data
        else:
            self._data = {}

    def sub(self, key: str) -> ConfigView:
        """Get a sub-section as a new ConfigView.

        Args:
            key: The key to look up in the current mapping.

        Returns:
            A new ConfigView wrapping the value at key, or empty if not found.
        """
        value = self._data.get(key)
        return ConfigView(value)

    def raw(self) -> Mapping[str, object]:
        """Return the underlying mapping.

        Returns:
            The raw Mapping[str, object] backing this ConfigView.
        """
        return self._data

    def s(self, key: str, default: str = "") -> str:
        """Get a string value.

        Args:
            key: The key to look up.
            default: Default value if key not found or value is None.

        Returns:
            The value as a string (stripped), or default if None/missing.
        """
        value = self._data.get(key)
        if value is None:
            return default
        return str(value).strip()

    def i(self, key: str, default: int = 0) -> int:
        """Get an integer value.

        Args:
            key: The key to look up.
            default: Default value if key not found, value is None, or conversion fails.

        Returns:
            The value as an int, or default on error.
        """
        value = self._data.get(key)
        if value is None:
            return default
        try:
            return int(str(value))
        except (ValueError, TypeError):
            return default

    def b(self, key: str, default: bool = False) -> bool:
        """Get a boolean value.

        Args:
            key: The key to look up.
            default: Default value if key not found or value is None.

        Returns:
            The value as a bool. Strings "true", "1", "yes" (case-insensitive)
            are treated as True. Other strings are False. Non-strings use bool().
        """
        value = self._data.get(key)
        if value is None:
            return default
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    def ls(self, key: str, default: list[str] | None = None) -> list[str]:
        """Get a list of strings.

        Args:
            key: The key to look up.
            default: Default value if key not found or value is None.
                     Defaults to empty list if not specified.

        Returns:
            A list of strings. If value is a string, returns [value].
            If value is iterable, filters to non-empty string items.
            Returns default (or []) if None/missing.
        """
        if default is None:
            default = []

        value = self._data.get(key)
        if value is None:
            return default

        if isinstance(value, str):
            return [value]

        try:
            items = cast(list[object], value)
            result = [str(item).strip() for item in items if str(item).strip()]
            return result
        except TypeError:
            return default
