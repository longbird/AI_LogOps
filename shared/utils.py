from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def load_dotenv(env_path: str | Path | None = None) -> dict[str, str]:
    """`.env` 파일을 읽어 `os.environ`에 로드한다 (python-dotenv 불필요).

    이미 존재하는 환경변수는 덮어쓰지 않는다 (``os.environ.setdefault``).
    반환값: 실제로 설정된 {key: value} 딕셔너리.
    """
    if env_path is None:
        # 실행 파일 기준으로 .env 탐색
        env_path = Path(os.path.dirname(os.path.abspath(__file__))).parent / ".env"
    env_file = Path(env_path)
    loaded: dict[str, str] = {}
    if not env_file.exists():
        return loaded
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key:
            continue
        os.environ.setdefault(key, value)
        loaded[key] = value
    return loaded


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


class _DailySequenceHandler(logging.Handler):
    """YYYYMMDD_NN.txt 형식의 로그 파일 핸들러. 10MB 초과 시 NN 증가."""

    MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB

    def __init__(self, log_dir: str | Path, encoding: str = "utf-8") -> None:
        super().__init__()
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._encoding = encoding
        self._current_date: str = ""
        self._current_seq: int = 0
        self._stream: io.TextIOWrapper | None = None
        self._bytes_written: int = 0

    def _today(self) -> str:
        return datetime.now().strftime("%Y%m%d")

    def _open_next(self, date_str: str, seq: int) -> None:
        if self._stream is not None:
            self._stream.close()
        filename = f"{date_str}_{seq:02d}.txt"
        filepath = self._log_dir / filename
        self._stream = open(filepath, "a", encoding=self._encoding)
        self._bytes_written = filepath.stat().st_size if filepath.exists() else 0
        self._current_date = date_str
        self._current_seq = seq

    def _resolve_file(self) -> None:
        date_str = self._today()
        if date_str == self._current_date and self._stream is not None:
            if self._bytes_written < self.MAX_BYTES:
                return
            # 현재 파일이 10MB 초과 → 다음 시퀀스
            self._open_next(date_str, self._current_seq + 1)
            return
        # 날짜 변경 또는 최초 호출 → 기존 파일 탐색
        seq = 1
        while True:
            filepath = self._log_dir / f"{date_str}_{seq:02d}.txt"
            if not filepath.exists() or filepath.stat().st_size < self.MAX_BYTES:
                break
            seq += 1
        self._open_next(date_str, seq)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._resolve_file()
            msg = self.format(record) + "\n"
            raw = msg.encode(self._encoding, errors="replace")
            stream = self._stream
            if stream is None:
                return
            _ = stream.write(msg)
            stream.flush()
            self._bytes_written += len(raw)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()


# 전역 파일 핸들러 (프로세스 당 하나)
_file_handler: _DailySequenceHandler | None = None


def setup_file_logging(log_dir: str | Path) -> None:
    """파일 로깅 활성화. 모든 로거에 파일 핸들러를 추가한다.

    서비스 시작 시 한 번만 호출하면 이후 setup_logging()으로 생성되는
    모든 로거에 자동으로 파일 핸들러가 붙는다.
    """
    global _file_handler  # noqa: PLW0603
    if _file_handler is not None:
        return
    _file_handler = _DailySequenceHandler(log_dir)
    formatter = logging.Formatter("[%(asctime)s] %(name)s %(levelname)s: %(message)s")
    _file_handler.setFormatter(formatter)
    # 이미 생성된 로거들에도 핸들러 추가
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger) and logger.handlers:
            logger.addHandler(_file_handler)


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
        # 파일 핸들러가 활성화되어 있으면 자동 추가
        if _file_handler is not None:
            logger.addHandler(_file_handler)

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
