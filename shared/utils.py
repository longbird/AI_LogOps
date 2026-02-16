from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def compute_sha256(filepath: str) -> str:
    digest = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_yaml_config(filepath: str) -> dict[str, Any]:
    with open(filepath, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise TypeError("YAML config root must be a mapping")
    return loaded


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "[%(asctime)s] %(name)s %(levelname)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def audit_log(
    action: str,
    agent_id: str,
    user: str,
    detail: str,
    log_path: str = "storage/audit.jsonl",
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "agent_id": agent_id,
        "user": user,
        "detail": detail,
    }
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
