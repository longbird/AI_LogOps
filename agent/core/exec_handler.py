"""원격 커맨드 실행 핸들러."""
from __future__ import annotations

import asyncio
import subprocess
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.protocol import (
    CmdExecAckPayload,
    CmdExecPayload,
    PacketType,
)
from shared.utils import setup_logging

if TYPE_CHECKING:
    from agent.core.tcp_client import TCPClient


class ExecHandler:
    """서버로부터 원격 커맨드 실행 요청을 처리합니다.

    보안: config.yaml의 remote_commands에 사전 등록된 커맨드만 실행 가능.
    config 변경 시 자동 reload.
    """

    def __init__(
        self,
        tcp_client: TCPClient,
        remote_commands: list[dict[str, Any]],
        config_path: Path | None = None,
    ) -> None:
        self._tcp_client = tcp_client
        self._config_path = config_path
        self._commands: dict[str, dict[str, Any]] = {}
        self._load_commands(remote_commands)
        self._logger: Logger = setup_logging("exec_handler")
        if self._commands:
            self._logger.info(
                "remote commands registered: %s",
                list(self._commands.keys()),
            )

    def _load_commands(self, remote_commands: list[dict[str, Any]]) -> None:
        self._commands.clear()
        for cmd in remote_commands:
            name = cmd.get("name", "")
            if name:
                self._commands[name] = cmd

    def _reload_from_config(self) -> None:
        """config.yaml에서 remote_commands를 다시 로드합니다."""
        if self._config_path is None or not self._config_path.exists():
            return
        try:
            import yaml
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            cmds = data.get("remote_commands", [])
            if cmds and isinstance(cmds, list):
                self._load_commands(cmds)
                self._logger.info(
                    "remote commands reloaded: %s",
                    list(self._commands.keys()),
                )
        except Exception as e:
            self._logger.warning("config reload failed: %s", e)

    async def handle_cmd_exec(self, payload_bytes: bytes) -> None:
        """CMD_EXEC 패킷 처리."""
        try:
            cmd = CmdExecPayload.unpack(payload_bytes)
        except (ValueError, Exception) as e:
            self._logger.warning("invalid CMD_EXEC payload: %s", e)
            return

        command_name = cmd.command_name
        self._logger.info("exec request: %s", command_name)

        # 등록된 커맨드인지 확인 (없으면 config reload 후 재시도)
        cmd_config = self._commands.get(command_name)
        if cmd_config is None:
            self._reload_from_config()
            cmd_config = self._commands.get(command_name)
        if cmd_config is None:
            self._logger.warning("unknown command: %s", command_name)
            await self._send_ack(
                command_name=command_name,
                success=False,
                error=f"등록되지 않은 커맨드: {command_name}",
            )
            return

        await self._execute_command(command_name, cmd_config)

    async def _execute_command(
        self, command_name: str, cmd_config: dict[str, Any]
    ) -> None:
        """커맨드를 실행하고 결과를 전송합니다."""
        command = cmd_config.get("command", "")
        args = cmd_config.get("args", [])
        working_dir = cmd_config.get("working_dir", None)
        timeout = cmd_config.get("timeout", 60)

        if not command:
            await self._send_ack(
                command_name=command_name,
                success=False,
                error="command가 비어있습니다",
            )
            return

        full_cmd = [command] + args if args else [command]
        self._logger.info("executing: %s (cwd=%s, timeout=%d)", full_cmd, working_dir, timeout)

        try:
            result = await asyncio.to_thread(
                self._run_subprocess, full_cmd, working_dir, timeout
            )
            await self._send_ack(
                command_name=command_name,
                success=result["success"],
                exit_code=result["exit_code"],
                output=result["output"],
                error=result["error"],
            )
        except Exception as e:
            self._logger.exception("command execution failed: %s", e)
            await self._send_ack(
                command_name=command_name,
                success=False,
                error=str(e),
            )

    def _run_subprocess(
        self,
        cmd: list[str],
        working_dir: str | None,
        timeout: int,
    ) -> dict[str, Any]:
        """subprocess 실행 (blocking, asyncio.to_thread에서 호출)."""
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=working_dir,
                timeout=timeout,
                shell=False,
            )
            output = proc.stdout[-2000:] if len(proc.stdout) > 2000 else proc.stdout
            error = proc.stderr[-2000:] if len(proc.stderr) > 2000 else proc.stderr
            return {
                "success": proc.returncode == 0,
                "exit_code": proc.returncode,
                "output": output,
                "error": error,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "error": f"타임아웃 ({timeout}초)",
            }
        except FileNotFoundError:
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "error": f"명령어를 찾을 수 없습니다: {cmd[0]}",
            }

    async def _send_ack(
        self,
        command_name: str,
        success: bool,
        exit_code: int = 0,
        output: str = "",
        error: str = "",
    ) -> None:
        """CMD_EXEC_ACK 전송."""
        ack = CmdExecAckPayload(
            command_name=command_name,
            success=success,
            exit_code=exit_code,
            output=output,
            error=error,
        )
        await self._tcp_client.send_packet(PacketType.CMD_EXEC_ACK, ack.pack())
        self._logger.info(
            "exec ack sent: %s success=%s exit_code=%d",
            command_name,
            success,
            exit_code,
        )
