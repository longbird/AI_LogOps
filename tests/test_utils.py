from __future__ import annotations

# pyright: reportMissingImports=false

import json

from shared.utils import audit_log, compute_sha256, load_yaml_config


def test_compute_sha256_known_input() -> None:
    test_file = "tests/fixtures_sha_hello.txt"
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("hello world")
    try:
        assert (
            compute_sha256(test_file)
            == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )
    finally:
        import os

        os.remove(test_file)


def test_compute_sha256_empty_file() -> None:
    test_file = "tests/fixtures_sha_empty.txt"
    with open(test_file, "w", encoding="utf-8"):
        pass
    try:
        assert (
            compute_sha256(test_file)
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
    finally:
        import os

        os.remove(test_file)


def test_load_yaml_config_loads_valid_yaml(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "host: localhost\nport: 9000\nfeatures:\n  audit: true\n", encoding="utf-8"
    )
    loaded = load_yaml_config(str(config_path))
    assert loaded == {"host": "localhost", "port": 9000, "features": {"audit": True}}


def test_audit_log_appends_entries_correctly(tmp_path) -> None:
    log_path = tmp_path / "audit" / "audit.jsonl"
    audit_log("deploy", "agent-01", "alice", "first", log_path=str(log_path))
    audit_log("deploy", "agent-01", "alice", "second", log_path=str(log_path))

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_audit_log_entry_has_correct_fields(tmp_path) -> None:
    log_path = tmp_path / "audit.jsonl"
    audit_log("restart", "agent-02", "bob", "manual restart", log_path=str(log_path))

    line = log_path.read_text(encoding="utf-8").strip()
    entry = json.loads(line)

    assert set(entry.keys()) == {"ts", "action", "agent_id", "user", "detail"}
    assert entry["action"] == "restart"
    assert entry["agent_id"] == "agent-02"
    assert entry["user"] == "bob"
    assert entry["detail"] == "manual restart"


def test_audit_log_creates_parent_directory_if_missing(tmp_path) -> None:
    log_path = tmp_path / "missing" / "dir" / "audit.jsonl"
    audit_log("update", "agent-03", "carol", "updating", log_path=str(log_path))
    assert log_path.exists()
