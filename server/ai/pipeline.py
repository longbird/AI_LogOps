from __future__ import annotations

import logging
import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

from .provider import BaseAIProvider
from server.storage.manager import StorageManager
from shared.utils import setup_logging


class PromptLoaderProtocol(Protocol):
    def __init__(self, templates_dir: str | None = None) -> None: ...

    def render_analyze(self, log_content: str) -> str: ...

    def render_plan(self, analysis_report: str) -> str: ...

    def render_fix(self, improvement_plan: str, source_code: str) -> str: ...


prompt_loader_module: ModuleType = importlib.import_module("server.ai.prompt_loader")
PromptLoader = cast(
    type[PromptLoaderProtocol],
    getattr(prompt_loader_module, "PromptLoader"),
)


@dataclass
class PipelineResult:
    analysis_report: str
    improvement_plan: str
    fixed_code: str
    validation_report: dict | None = None  # pyright: ignore[reportMissingTypeArgument]


class AIPipeline:
    def __init__(
        self,
        provider: BaseAIProvider,
        prompt_loader: PromptLoaderProtocol | None = None,
        storage_dir: str = "./storage",
    ) -> None:
        self.provider: BaseAIProvider = provider
        self.prompt_loader: PromptLoaderProtocol = prompt_loader or PromptLoader()
        self.review_provider: BaseAIProvider | None = None
        self._agent_id: str = "ai_pipeline"
        self._storage_root: Path = Path(storage_dir)
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._storage_manager: StorageManager = StorageManager(
            base_dir=str(self._storage_root)
        )
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)

    async def step1_analyze(self, log_content: str) -> str:
        prompt = self.prompt_loader.render_analyze(log_content)
        analysis_report = await self.provider.generate(
            prompt=prompt,
            system="시스템 로그 분석 전문가",
        )
        _ = self._storage_manager.save_report(
            agent_id=self._agent_id,
            report_name="Analysis_Report.md",
            content=analysis_report,
        )
        self._logger.info("analysis report generated and saved")
        return analysis_report

    async def step2_plan(self, analysis_report: str) -> str:
        prompt = self.prompt_loader.render_plan(analysis_report)
        improvement_plan = await self.provider.generate(
            prompt=prompt,
            system="시스템 개선 계획 수립 전문가",
        )
        _ = self._storage_manager.save_report(
            agent_id=self._agent_id,
            report_name="Improvement_Plan.md",
            content=improvement_plan,
        )
        self._logger.info("improvement plan generated and saved")
        return improvement_plan

    async def step3_fix(self, plan: str, source_code: str) -> str:
        prompt = self.prompt_loader.render_fix(plan, source_code)
        fixed_code = await self.provider.generate(
            prompt=prompt,
            system="Python 코드 수정 전문가",
        )
        _ = self._storage_manager.save_report(
            agent_id=self._agent_id,
            report_name="Fixed_Source_Code.py",
            content=fixed_code,
        )
        self._logger.info("fixed code generated and saved")
        return fixed_code

    async def run_full(self, log_content: str, source_code: str) -> PipelineResult:
        analysis_report = await self.step1_analyze(log_content)
        improvement_plan = await self.step2_plan(analysis_report)
        fixed_code = await self.step3_fix(improvement_plan, source_code)

        return PipelineResult(
            analysis_report=analysis_report,
            improvement_plan=improvement_plan,
            fixed_code=fixed_code,
        )
