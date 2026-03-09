"""에이전트 설정 원격 관리 핸들러."""
from __future__ import annotations

import copy
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from shared.crypto_utils import (
    apply_masked_update,
    decrypt_config,
    load_or_create_key,
    mask_config,
)
from shared.protocol import (
    CmdConfigPayload,
    ConfigAction,
    Packet,
    PacketType,
)
from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.tcp_client import TCPClient


class ConfigHandler:
    """서버로부터 설정 조회/업데이트 요청을 처리합니다."""

    def __init__(self, base_dir: Path, tcp_client: "TCPClient") -> None:
        self._base_dir = base_dir
        self._tcp_client = tcp_client
        self._logger = setup_logging(self.__class__.__name__)
        self._config_path = base_dir / "config.yaml"
        self._env_path = base_dir / ".env"

    async def handle_cmd_config(self, payload_bytes: bytes) -> None:
        """CMD_CONFIG 패킷 처리 콜백."""
        try:
            cmd = CmdConfigPayload.unpack(payload_bytes)
        except (ValueError, Exception) as e:
            self._logger.warning("invalid CMD_CONFIG payload: %s", e)
            return

        if cmd.action == ConfigAction.GET:
            await self._handle_get()
        elif cmd.action == ConfigAction.UPDATE:
            await self._handle_update(cmd.config_data)
        else:
            self._logger.warning("unknown config action: %s", cmd.action)

    async def _handle_get(self) -> None:
        """설정 파일을 읽어 마스킹한 후 서버로 전송합니다."""
        try:
            config = self._load_config()
            # Decrypt any ENC: values first so we get the real structure,
            # then mask sensitive values for server display
            try:
                fernet = load_or_create_key(str(self._env_path))
                config = decrypt_config(config, fernet)
            except Exception:
                pass  # No encryption key or not encrypted — send as-is

            masked = mask_config(config)
            config_json = json.dumps(masked, ensure_ascii=False, default=str)

            ack = CmdConfigPayload(
                action=ConfigAction.GET,
                config_data=config_json,
            )
            await self._tcp_client.send_packet(
                PacketType.CMD_CONFIG_ACK, ack.pack()
            )
            self._logger.info("config GET response sent (size=%d)", len(config_json))
        except Exception as e:
            self._logger.exception("config GET failed: %s", e)
            # Send error response
            error_data = json.dumps({"error": str(e)}, ensure_ascii=False)
            ack = CmdConfigPayload(action=ConfigAction.GET, config_data=error_data)
            await self._tcp_client.send_packet(
                PacketType.CMD_CONFIG_ACK, ack.pack()
            )

    async def _handle_update(self, config_data: str) -> None:
        """서버로부터 받은 설정을 기존 설정에 병합하여 저장합니다.

        - MASK_VALUE("********")인 필드는 기존 값 유지
        - 변경된 필드만 업데이트
        - 저장 전 백업 생성
        - 저장 후 변경 섹션 목록을 ACK로 전송
        """
        result: dict[str, Any] = {"success": False, "message": "", "changed_sections": [], "needs_restart": False}

        try:
            updated = json.loads(config_data)

            # Load current config (with decryption)
            current = self._load_config()
            try:
                fernet = load_or_create_key(str(self._env_path))
                current_decrypted = decrypt_config(current, fernet)
            except Exception:
                fernet = None
                current_decrypted = copy.deepcopy(current)

            # Detect changed sections (top-level keys)
            changed_sections = []
            for key in updated:
                if key not in current_decrypted:
                    changed_sections.append(key)
                elif updated[key] != current_decrypted.get(key):
                    # Check if it's just masked values (no real change)
                    if isinstance(updated[key], dict) and isinstance(current_decrypted.get(key), dict):
                        merged_section = apply_masked_update(current_decrypted[key], updated[key])
                        if merged_section != current_decrypted[key]:
                            changed_sections.append(key)
                    else:
                        changed_sections.append(key)

            if not changed_sections:
                result["success"] = True
                result["message"] = "변경된 설정이 없습니다."
            else:
                # Merge: masked values keep original, real values get updated
                merged = apply_masked_update(current_decrypted, updated)

                # Backup current config
                self._backup_config()

                # Encrypt sensitive fields before saving
                if fernet is not None:
                    from shared.crypto_utils import encrypt_config
                    save_config = encrypt_config(merged, fernet)
                else:
                    save_config = merged

                # Save
                self._save_config(save_config)

                # Check if connection section changed (needs restart)
                needs_restart = "connection" in changed_sections or "servers" in changed_sections

                result["success"] = True
                result["message"] = f"설정이 업데이트되었습니다. 변경: {', '.join(changed_sections)}"
                result["changed_sections"] = changed_sections
                result["needs_restart"] = needs_restart

                self._logger.info("config updated: changed=%s needs_restart=%s", changed_sections, needs_restart)
        except Exception as e:
            self._logger.exception("config UPDATE failed: %s", e)
            result["message"] = f"설정 업데이트 실패: {e}"

        # Send ACK
        ack_data = json.dumps(result, ensure_ascii=False)
        ack = CmdConfigPayload(action=ConfigAction.UPDATE, config_data=ack_data)
        await self._tcp_client.send_packet(PacketType.CMD_CONFIG_ACK, ack.pack())

    def _load_config(self) -> dict:
        """config.yaml을 로드합니다."""
        with open(self._config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}

    def _save_config(self, config: dict) -> None:
        """config.yaml에 저장합니다."""
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.dump(
                config,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def _backup_config(self) -> None:
        """config.yaml의 백업을 생성합니다."""
        if not self._config_path.exists():
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self._config_path.with_suffix(f".{timestamp}.bak")
        shutil.copy2(self._config_path, backup_path)
        self._logger.info("config backup: %s", backup_path.name)

        # Keep only last 5 backups
        backups = sorted(self._config_path.parent.glob("config.*.bak"))
        for old in backups[:-5]:
            old.unlink(missing_ok=True)
