from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportImplicitOverride=false

import json
from pathlib import Path
from typing import cast

import pytest

from server.ai.pipeline import AIPipeline, PipelineResult
from server.ai.provider import BaseAIProvider


class MockAIProvider(BaseAIProvider):
    def __init__(self, responses: list[str] | None = None):
        super().__init__(api_key="test", model="mock")
        self.provider_name: str = "mock"
        self._responses: list[str] = responses or []
        self._call_count: int = 0

    async def _call_api(self, prompt: str, system: str = "") -> str:
        if self._call_count < len(self._responses):
            result = self._responses[self._call_count]
            self._call_count += 1
            return result
        return "mock response"


def _load_response(file_name: str) -> str:
    fixture_path = Path(__file__).parent / "fixtures" / "ai_responses" / file_name
    payload = cast(dict[str, str], json.loads(fixture_path.read_text(encoding="utf-8")))
    return payload["content"]


def _load_raw_response(file_name: str) -> str:
    fixture_path = Path(__file__).parent / "fixtures" / "ai_responses" / file_name
    return fixture_path.read_text(encoding="utf-8")


@pytest.fixture
def mock_ai_responses() -> dict[str, str]:
    return {
        "analyze": _load_response("analyze_response.json"),
        "plan": _load_response("plan_response.json"),
        "fix": _load_response("fix_response.json"),
        "review": _load_raw_response("review_response.json"),
    }


@pytest.fixture
def pipeline(mock_ai_responses: dict[str, str], tmp_path: Path) -> AIPipeline:
    provider = MockAIProvider(
        responses=[
            mock_ai_responses["analyze"],
            mock_ai_responses["plan"],
            mock_ai_responses["fix"],
            mock_ai_responses["review"],
        ]
    )
    return AIPipeline(provider=provider, storage_dir=str(tmp_path))


@pytest.mark.asyncio
class TestAIPipeline:
    async def test_step1_analyze(
        self,
        mock_ai_responses: dict[str, str],
        tmp_path: Path,
    ) -> None:
        pipeline = AIPipeline(
            provider=MockAIProvider(responses=[mock_ai_responses["analyze"]]),
            storage_dir=str(tmp_path),
        )
        analysis = await pipeline.step1_analyze("ERROR: NullPointerException")

        assert "# Analysis Report" in analysis
        saved = tmp_path / "reports" / "ai_pipeline" / "Analysis_Report.md"
        assert saved.exists()
        assert saved.read_text(encoding="utf-8") == analysis

    async def test_step2_plan(
        self,
        mock_ai_responses: dict[str, str],
        tmp_path: Path,
    ) -> None:
        pipeline = AIPipeline(
            provider=MockAIProvider(responses=[mock_ai_responses["plan"]]),
            storage_dir=str(tmp_path),
        )
        plan = await pipeline.step2_plan("# Analysis Report\n\nRoot cause")

        assert "# Improvement Plan" in plan
        saved = tmp_path / "reports" / "ai_pipeline" / "Improvement_Plan.md"
        assert saved.exists()
        assert saved.read_text(encoding="utf-8") == plan

    async def test_step3_fix(
        self,
        mock_ai_responses: dict[str, str],
        tmp_path: Path,
    ) -> None:
        pipeline = AIPipeline(
            provider=MockAIProvider(responses=[mock_ai_responses["fix"]]),
            storage_dir=str(tmp_path),
        )
        fixed = await pipeline.step3_fix("# Improvement Plan", "def old():\n    pass\n")

        assert "def get_connection" in fixed
        saved = tmp_path / "reports" / "ai_pipeline" / "Fixed_Source_Code.py"
        assert saved.exists()
        assert saved.read_text(encoding="utf-8") == fixed

    async def test_full_pipeline(self, pipeline: AIPipeline) -> None:
        result = await pipeline.run_full(
            log_content="ERROR: ConnectionTimeout",
            source_code="def get_connection():\n    return None\n",
        )

        assert isinstance(result, PipelineResult)
        assert "# Analysis Report" in result.analysis_report
        assert "# Improvement Plan" in result.improvement_plan
        assert "# Fixed Code" in result.fixed_code
        assert result.validation_report is not None

    async def test_intermediate_files_saved(
        self,
        pipeline: AIPipeline,
        tmp_path: Path,
    ) -> None:
        _ = await pipeline.run_full(
            log_content="ERROR: ConnectionTimeout",
            source_code="def get_connection():\n    return None\n",
        )

        assert (tmp_path / "reports" / "ai_pipeline" / "Analysis_Report.md").exists()
        assert (tmp_path / "reports" / "ai_pipeline" / "Improvement_Plan.md").exists()
        assert (tmp_path / "reports" / "ai_pipeline" / "Fixed_Source_Code.py").exists()


@pytest.mark.asyncio
class TestStep35Validation:
    async def test_syntax_check_pass(
        self,
        mock_ai_responses: dict[str, str],
        tmp_path: Path,
    ) -> None:
        pipeline = AIPipeline(
            provider=MockAIProvider(responses=[mock_ai_responses["review"]]),
            storage_dir=str(tmp_path),
        )

        report = await pipeline.step3_5_validate(
            original="def old():\n    return 1\n",
            fixed="def new():\n    return 2\n",
            plan="# Improvement Plan",
        )

        assert report["syntax_check"] == "PASS"

    async def test_syntax_check_fail(self, tmp_path: Path) -> None:
        pipeline = AIPipeline(provider=MockAIProvider(), storage_dir=str(tmp_path))

        report = await pipeline.step3_5_validate(
            original="def old():\n    return 1\n",
            fixed="def broken(:\n    return 2\n",
            plan="# Improvement Plan",
        )

        assert report["syntax_check"] == "FAIL"
        assert report["deploy_allowed"] is False
        assert report["requires_manual_approval"] is True

    async def test_cross_review_uses_review_provider(
        self,
        mock_ai_responses: dict[str, str],
        tmp_path: Path,
    ) -> None:
        primary = MockAIProvider(responses=["{}"])
        primary.provider_name = "primary-mock"
        reviewer = MockAIProvider(responses=[mock_ai_responses["review"]])
        reviewer.provider_name = "review-mock"

        pipeline = AIPipeline(provider=primary, storage_dir=str(tmp_path))
        pipeline.review_provider = reviewer

        report = await pipeline.step3_5_validate(
            original="def old():\n    return 1\n",
            fixed="def new():\n    return 2\n",
            plan="# Improvement Plan",
        )

        cross_review = cast(dict[str, object], report["cross_review"])
        assert cross_review["reviewer"] == "review-mock"
        assert cross_review["reviewer"] != "primary-mock"

    async def test_confidence_above_70_allows_deploy(
        self,
        mock_ai_responses: dict[str, str],
        tmp_path: Path,
    ) -> None:
        pipeline = AIPipeline(
            provider=MockAIProvider(responses=[mock_ai_responses["review"]]),
            storage_dir=str(tmp_path),
        )

        report = await pipeline.step3_5_validate(
            original="def old():\n    return 1\n",
            fixed="def new():\n    return 2\n",
            plan="# Improvement Plan",
        )

        assert report["confidence_score"] == 87
        assert report["deploy_allowed"] is True

    async def test_confidence_below_70_requires_manual(self, tmp_path: Path) -> None:
        pipeline = AIPipeline(
            provider=MockAIProvider(
                responses=['{"severity":"MEDIUM","issues":[],"confidence_score":55}']
            ),
            storage_dir=str(tmp_path),
        )

        report = await pipeline.step3_5_validate(
            original="def old():\n    return 1\n",
            fixed="def new():\n    return 2\n",
            plan="# Improvement Plan",
        )

        assert report["confidence_score"] == 55
        assert report["requires_manual_approval"] is True
        assert report["deploy_allowed"] is False

    async def test_full_pipeline_includes_validation(
        self,
        mock_ai_responses: dict[str, str],
        tmp_path: Path,
    ) -> None:
        pipeline = AIPipeline(
            provider=MockAIProvider(
                responses=[
                    mock_ai_responses["analyze"],
                    mock_ai_responses["plan"],
                    mock_ai_responses["fix"],
                    mock_ai_responses["review"],
                ]
            ),
            storage_dir=str(tmp_path),
        )

        result = await pipeline.run_full(
            log_content="ERROR: ConnectionTimeout",
            source_code="def get_connection():\n    return None\n",
        )

        assert result.validation_report is not None
        assert result.validation_report["syntax_check"] == "PASS"

    async def test_validation_report_saved(
        self,
        mock_ai_responses: dict[str, str],
        tmp_path: Path,
    ) -> None:
        pipeline = AIPipeline(
            provider=MockAIProvider(responses=[mock_ai_responses["review"]]),
            storage_dir=str(tmp_path),
        )

        _ = await pipeline.step3_5_validate(
            original="def old():\n    return 1\n",
            fixed="def new():\n    return 2\n",
            plan="# Improvement Plan",
        )

        saved = tmp_path / "reports" / "ai_pipeline" / "Validation_Report.json"
        assert saved.exists()
