"""MySQL connection management (single cached connection)."""

from __future__ import annotations

import logging

import pymysql
import pymysql.cursors

from shared.utils import setup_logging

logger: logging.Logger = setup_logging("db_connection")

_conn: pymysql.connections.Connection | None = None


def get_connection(config: dict[str, object]) -> pymysql.connections.Connection:
    """Return a cached MySQL connection, reconnecting if necessary.

    Parameters
    ----------
    config:
        Dict with keys ``host``, ``port``, ``user``, ``password``, ``database``
        (loaded from ``recording.db`` section of *config.yaml*).
    """
    global _conn  # noqa: PLW0603

    if _conn is not None:
        try:
            _conn.ping(reconnect=True)
            return _conn
        except Exception:
            logger.warning("Cached DB connection lost, reconnecting …")
            _conn = None

    logger.info(
        "Connecting to MySQL %s:%s/%s",
        config.get("host", "127.0.0.1"),
        config.get("port", 3306),
        config.get("database", "airrec"),
    )

    _conn = pymysql.connect(
        host=str(config.get("host", "127.0.0.1")),
        port=int(str(config.get("port", 3306))),
        user=str(config.get("user", "root")),
        password=str(config.get("password", "")),
        database=str(config.get("database", "airrec")),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    return _conn


def close_pool() -> None:
    """Close the cached connection if it is open."""
    global _conn  # noqa: PLW0603

    if _conn is not None:
        try:
            _conn.close()
            logger.info("MySQL connection closed.")
        except Exception:
            logger.debug("Error while closing MySQL connection", exc_info=True)
        finally:
            _conn = None
