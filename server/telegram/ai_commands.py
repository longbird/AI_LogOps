from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import logging
from pathlib import Path
from typing import Protocol, cast

from server.ai.pipeline import AIPipeline, PipelineResult
from server.core.session_mgr import SessionManager
from server.telegram.handler import ParsedCommand, TelegramHandler
from shared.utils import setup_logging


class _StorageManagerLike(Protocol):
    base_dir: str | Path


class AICommandHandler:
    """AI 파이프라인 명령 핸들러. 스펙 섹션 6 참조."""

    def __init__(self, pipeline: AIPipeline, session_mgr: SessionManager):
        self.pipeline: AIPipeline = pipeline
        self.session_mgr: SessionManager = session_mgr
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)

    def register_all(self, handler: TelegramHandler) -> None:
        """모든 AI 명령을 TelegramHandler에 등록."""
        handler.register("analyze", self.cmd_analyze)
        handler.register("plan", self.cmd_plan)
        handler.register("fix", self.cmd_fix)
        handler.register("auto", self.cmd_auto)

    def _read_latest_report(self, agent_id: str, report_name: str) -> str | None:
        storage_mgr = cast(
            _StorageManagerLike | None,
            getattr(self.pipeline, "_storage_manager", None),
        )
        if storage_mgr is None:
            self._logger.warning("pipeline storage manager missing")
            return None

        base_dir = Path(storage_mgr.base_dir)
        report_path = base_dir / "reports" / agent_id / report_name
        if not report_path.exists():
            return None
        return report_path.read_text(encoding="utf-8")

    def _get_log_content(self, agent_id: str) -> str | None:
        session = self.session_mgr.get_session(agent_id)
        if session is None or not session.log_buffer:
            return None
        return "\n".join(session.log_buffer)

    async def cmd_analyze(self, cmd: ParsedCommand) -> str:
        """/analyze <agent_id>: 최근 로그 기반 Step 1 분석 실행."""
        if not cmd.args:
            return "Usage: /analyze <agent_id>"

        agent_id = cmd.args[0]
        log_content = self._get_log_content(agent_id)
        if log_content is None:
            return f"No logs available for {agent_id}"

        report = await self.pipeline.step1_analyze(log_content)
        return f"Analysis complete for {agent_id}.\n\n{report[:500]}..."

    async def cmd_plan(self, cmd: ParsedCommand) -> str:
        """/plan <agent_id>: 마지막 분석 리포트 기반 Step 2 계획 생성."""
        if not cmd.args:
            return "Usage: /plan <agent_id>"

        agent_id = cmd.args[0]
        analysis = self._read_latest_report(agent_id, "Analysis_Report.md")
        if analysis is None:
            return f"Analysis report not found for {agent_id}"

        plan = await self.pipeline.step2_plan(analysis)
        return f"Plan complete for {agent_id}.\n\n{plan[:500]}..."

    async def cmd_fix(self, cmd: ParsedCommand) -> str:
        """/fix <agent_id> [source_code_path]: Step 3 코드 수정 실행."""
        if not cmd.args:
            return "Usage: /fix <agent_id> [source_code_path]"

        agent_id = cmd.args[0]
        plan = self._read_latest_report(agent_id, "Improvement_Plan.md")
        if plan is None:
            return f"Improvement plan not found for {agent_id}"

        source_code = "# source code placeholder"
        if len(cmd.args) >= 2:
            source_path = Path(cmd.args[1])
            if source_path.exists():
                source_code = source_path.read_text(encoding="utf-8")

        fixed = await self.pipeline.step3_fix(plan, source_code)
        return f"Fix complete for {agent_id}.\n\n{fixed[:500]}..."

    async def cmd_auto(self, cmd: ParsedCommand) -> str:
        """/auto <agent_id> [source_code]: 전체 파이프라인 실행."""
        if not cmd.args:
            return "Usage: /auto <agent_id> [source_code]"

        agent_id = cmd.args[0]
        log_content = self._get_log_content(agent_id)
        if log_content is None:
            return f"No logs available for {agent_id}"

        source_code = " ".join(cmd.args[1:]) if len(cmd.args) > 1 else "# source code"
        result: PipelineResult = await self.pipeline.run_full(log_content, source_code)

        validation = result.validation_report or {}
        confidence = validation.get("confidence_score", 0)
        deploy_allowed = validation.get("deploy_allowed", False)

        response = (
            f"Auto pipeline complete for {agent_id}.\n\n"
            f"Confidence: {confidence}\n"
            f"Deploy Allowed: {deploy_allowed}"
        )

        if isinstance(confidence, int) and confidence < 70:
            response += "\n⚠️ Manual approval required"

        return response
