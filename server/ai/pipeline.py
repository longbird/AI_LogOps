from __future__ import annotations

import logging
import importlib
import json
import ast
import difflib
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

    def render_review(
        self,
        original_code: str,
        fixed_code: str,
        improvement_plan: str,
    ) -> str: ...


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

    async def step3_5_validate(
        self,
        original: str,
        fixed: str,
        plan: str,
    ) -> dict[str, object]:
        """Step 3.5: Automated Validation. Spec section 4.2 v2.1."""
        report: dict[str, object] = {}

        try:
            _ = ast.parse(fixed)
            report["syntax_check"] = "PASS"
        except SyntaxError as error:
            report["syntax_check"] = "FAIL"
            report["syntax_error"] = str(error)
            report["deploy_allowed"] = False
            report["requires_manual_approval"] = True
            self._save_validation_report(report)
            return report

        reviewer = self.review_provider or self.provider
        review_prompt = self.prompt_loader.render_review(
            original_code=original,
            fixed_code=fixed,
            improvement_plan=plan,
        )
        review_result_str = await reviewer.generate(
            prompt=review_prompt,
            system="시니어 코드 리뷰어",
        )
        try:
            cross_review_obj = cast(object, json.loads(review_result_str))
            if isinstance(cross_review_obj, dict):
                cross_review: dict[str, object] = {
                    str(key): value
                    for key, value in cast(
                        dict[object, object],
                        cross_review_obj,
                    ).items()
                }
            else:
                cross_review = {
                    "reviewer": reviewer.provider_name,
                    "severity": "UNKNOWN",
                    "issues": [],
                    "confidence_score": 50,
                }
        except json.JSONDecodeError:
            cross_review = {
                "reviewer": reviewer.provider_name,
                "severity": "UNKNOWN",
                "issues": [],
                "confidence_score": 50,
            }

        cross_review["reviewer"] = reviewer.provider_name
        report["cross_review"] = cross_review

        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(),
                fixed.splitlines(),
                lineterm="",
            )
        )
        report["diff_summary"] = "\n".join(diff_lines)

        score_obj = cross_review.get("confidence_score", 0)
        normalized_score = score_obj if isinstance(score_obj, int) else 0
        report["confidence_score"] = normalized_score
        report["deploy_allowed"] = normalized_score >= 70
        report["requires_manual_approval"] = normalized_score < 70

        self._save_validation_report(report)
        return report

    def _save_validation_report(self, report: dict[str, object]) -> None:
        _ = self._storage_manager.save_report(
            agent_id=self._agent_id,
            report_name="Validation_Report.json",
            content=json.dumps(report, ensure_ascii=False, indent=2),
        )
        self._logger.info("validation report generated and saved")

    async def run_full(self, log_content: str, source_code: str) -> PipelineResult:
        analysis_report = await self.step1_analyze(log_content)
        improvement_plan = await self.step2_plan(analysis_report)
        fixed_code = await self.step3_fix(improvement_plan, source_code)
        validation = await self.step3_5_validate(
            original=source_code,
            fixed=fixed_code,
            plan=improvement_plan,
        )

        return PipelineResult(
            analysis_report=analysis_report,
            improvement_plan=improvement_plan,
            fixed_code=fixed_code,
            validation_report=validation,
        )
