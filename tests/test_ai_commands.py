from __future__ import annotations

# pyright: reportMissingImports=false, reportArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from unittest.mock import AsyncMock

import pytest

from server.ai.pipeline import PipelineResult
from server.telegram.handler import ParsedCommand
from shared.models import AgentInfo, AgentSession, AgentState


@dataclass
class StorageStub:
    base_dir: Path


class PipelineStub:
    def __init__(
        self,
        analyze_result: str = "# Analysis",
        plan_result: str = "# Plan",
        fix_result: str = "def fixed():\n    return 1\n",
        auto_result: PipelineResult | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self._storage_manager: StorageStub = StorageStub(base_dir=base_dir or Path("."))
        self.analyze_calls: list[str] = []
        self.plan_calls: list[str] = []
        self.fix_calls: list[tuple[str, str]] = []
        self.auto_calls: list[tuple[str, str]] = []

        async def _step1(log_content: str) -> str:
            self.analyze_calls.append(log_content)
            return analyze_result

        async def _step2(analysis: str) -> str:
            self.plan_calls.append(analysis)
            return plan_result

        async def _step3(plan: str, source: str) -> str:
            self.fix_calls.append((plan, source))
            return fix_result

        async def _run_full(log_content: str, source: str) -> PipelineResult:
            self.auto_calls.append((log_content, source))
            if auto_result is not None:
                return auto_result
            return PipelineResult(
                analysis_report="# Analysis",
                improvement_plan="# Plan",
                fixed_code="def x():\n    return 1\n",
                validation_report={"confidence_score": 85, "deploy_allowed": True},
            )

        self.step1_analyze: AsyncMock = AsyncMock(side_effect=_step1)
        self.step2_plan: AsyncMock = AsyncMock(side_effect=_step2)
        self.step3_fix: AsyncMock = AsyncMock(side_effect=_step3)
        self.run_full: AsyncMock = AsyncMock(side_effect=_run_full)


class SessionManagerStub:
    def __init__(self, resolver: Callable[[str], AgentSession | None]) -> None:
        self._resolver: Callable[[str], AgentSession | None] = resolver

    def get_session(self, agent_id: str) -> AgentSession | None:
        return self._resolver(agent_id)


class RegisterRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def register(self, command: str, handler: object) -> None:
        self.calls.append((command, handler))


def _make_session(agent_id: str, logs: list[str]) -> AgentSession:
    return AgentSession(
        agent_info=AgentInfo(
            agent_id=agent_id,
            version="1.0.0",
            state=AgentState.CONNECTED,
        ),
        session_id="session-1",
        log_buffer=logs,
    )


@pytest.mark.asyncio
class TestAICommandHandler:
    async def test_cmd_analyze_with_logs(self) -> None:
        from server.telegram.ai_commands import AICommandHandler

        pipeline = PipelineStub(analyze_result="# Analysis\n" + ("A" * 600))
        session_mgr = SessionManagerStub(
            resolver=lambda _: _make_session("agent-1", ["line-1", "line-2"])
        )

        ai_handler = AICommandHandler(pipeline=pipeline, session_mgr=session_mgr)
        cmd = ParsedCommand(
            command="analyze",
            args=["agent-1"],
            chat_id=101,
            raw_text="/analyze agent-1",
        )

        response = await ai_handler.cmd_analyze(cmd)

        assert pipeline.analyze_calls == ["line-1\nline-2"]
        assert "Analysis complete for agent-1." in response

    async def test_cmd_analyze_no_agent(self) -> None:
        from server.telegram.ai_commands import AICommandHandler

        pipeline = PipelineStub()
        session_mgr = SessionManagerStub(resolver=lambda _: None)

        ai_handler = AICommandHandler(pipeline=pipeline, session_mgr=session_mgr)
        cmd = ParsedCommand(
            command="analyze",
            args=["missing-agent"],
            chat_id=101,
            raw_text="/analyze missing-agent",
        )

        response = await ai_handler.cmd_analyze(cmd)

        assert response == "No logs available for missing-agent"
        assert pipeline.analyze_calls == []

    async def test_cmd_analyze_no_logs(self) -> None:
        from server.telegram.ai_commands import AICommandHandler

        pipeline = PipelineStub()
        session_mgr = SessionManagerStub(
            resolver=lambda _: _make_session("agent-2", [])
        )

        ai_handler = AICommandHandler(pipeline=pipeline, session_mgr=session_mgr)
        cmd = ParsedCommand(
            command="analyze",
            args=["agent-2"],
            chat_id=101,
            raw_text="/analyze agent-2",
        )

        response = await ai_handler.cmd_analyze(cmd)

        assert response == "No logs available for agent-2"
        assert pipeline.analyze_calls == []

    async def test_cmd_plan(self, tmp_path: Path) -> None:
        from server.telegram.ai_commands import AICommandHandler

        report_dir = tmp_path / "reports" / "agent-3"
        report_dir.mkdir(parents=True, exist_ok=True)
        _ = (report_dir / "Analysis_Report.md").write_text(
            "# Analysis Report\n\nRoot cause",
            encoding="utf-8",
        )

        pipeline = PipelineStub(
            plan_result="# Improvement Plan\n\nPlan", base_dir=tmp_path
        )
        session_mgr = SessionManagerStub(resolver=lambda _: None)

        ai_handler = AICommandHandler(pipeline=pipeline, session_mgr=session_mgr)
        cmd = ParsedCommand(
            command="plan",
            args=["agent-3"],
            chat_id=101,
            raw_text="/plan agent-3",
        )

        response = await ai_handler.cmd_plan(cmd)

        assert pipeline.plan_calls == ["# Analysis Report\n\nRoot cause"]
        assert "Plan complete for agent-3." in response

    async def test_cmd_fix(self, tmp_path: Path) -> None:
        from server.telegram.ai_commands import AICommandHandler

        report_dir = tmp_path / "reports" / "agent-4"
        report_dir.mkdir(parents=True, exist_ok=True)
        _ = (report_dir / "Improvement_Plan.md").write_text(
            "# Improvement Plan\n\nDo this",
            encoding="utf-8",
        )

        source_path = tmp_path / "target.py"
        _ = source_path.write_text("def old():\n    return None\n", encoding="utf-8")

        pipeline = PipelineStub(base_dir=tmp_path)
        session_mgr = SessionManagerStub(resolver=lambda _: None)

        ai_handler = AICommandHandler(pipeline=pipeline, session_mgr=session_mgr)
        cmd = ParsedCommand(
            command="fix",
            args=["agent-4", str(source_path)],
            chat_id=101,
            raw_text=f"/fix agent-4 {source_path}",
        )

        response = await ai_handler.cmd_fix(cmd)

        assert pipeline.fix_calls == [
            ("# Improvement Plan\n\nDo this", "def old():\n    return None\n")
        ]
        assert "Fix complete for agent-4." in response

    async def test_cmd_auto(self) -> None:
        from server.telegram.ai_commands import AICommandHandler

        pipeline = PipelineStub(
            auto_result=PipelineResult(
                analysis_report="# Analysis",
                improvement_plan="# Plan",
                fixed_code="def x():\n    return 1\n",
                validation_report={"confidence_score": 85, "deploy_allowed": True},
            )
        )
        session_mgr = SessionManagerStub(
            resolver=lambda _: _make_session("agent-5", ["e1", "e2"])
        )

        ai_handler = AICommandHandler(pipeline=pipeline, session_mgr=session_mgr)
        cmd = ParsedCommand(
            command="auto",
            args=["agent-5", "print('hello')"],
            chat_id=101,
            raw_text="/auto agent-5 print('hello')",
        )

        response = await ai_handler.cmd_auto(cmd)

        assert pipeline.auto_calls == [("e1\ne2", "print('hello')")]
        assert "Auto pipeline complete for agent-5." in response
        assert "Confidence: 85" in response

    async def test_cmd_auto_low_confidence(self) -> None:
        from server.telegram.ai_commands import AICommandHandler

        pipeline = PipelineStub(
            auto_result=PipelineResult(
                analysis_report="# Analysis",
                improvement_plan="# Plan",
                fixed_code="def x():\n    return 1\n",
                validation_report={"confidence_score": 60, "deploy_allowed": False},
            )
        )
        session_mgr = SessionManagerStub(
            resolver=lambda _: _make_session("agent-6", ["error"])
        )

        ai_handler = AICommandHandler(pipeline=pipeline, session_mgr=session_mgr)
        cmd = ParsedCommand(
            command="auto",
            args=["agent-6", "print('hello')"],
            chat_id=101,
            raw_text="/auto agent-6 print('hello')",
        )

        response = await ai_handler.cmd_auto(cmd)

        assert "⚠️ Manual approval required" in response

    async def test_register_all(self) -> None:
        from server.telegram.ai_commands import AICommandHandler

        pipeline = PipelineStub()
        session_mgr = SessionManagerStub(resolver=lambda _: None)

        ai_handler = AICommandHandler(pipeline=pipeline, session_mgr=session_mgr)
        recorder = RegisterRecorder()

        ai_handler.register_all(recorder)

        registered_commands = {command for command, _ in recorder.calls}
        assert registered_commands == {"analyze", "plan", "fix", "auto"}
