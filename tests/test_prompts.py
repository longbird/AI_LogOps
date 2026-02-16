from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast


class _RaisesContext(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> bool | None: ...


class _PytestModule(Protocol):
    def raises(self, expected_exception: object) -> _RaisesContext: ...


pytest = cast(
    _PytestModule,
    cast(object, importlib.import_module("pytest")),
)
jinja2_module: ModuleType = importlib.import_module("jinja2")
TemplateNotFound = cast(type[Exception], getattr(jinja2_module, "TemplateNotFound"))


class PromptLoaderProtocol(Protocol):
    def __init__(self, templates_dir: str | None = None) -> None: ...

    def render(self, template_name: str, **variables: str) -> str: ...

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
    type[PromptLoaderProtocol], getattr(prompt_loader_module, "PromptLoader")
)


def test_render_analyze_template() -> None:
    loader = PromptLoader()

    rendered = loader.render_analyze(log_content="ERROR: database connection failed")

    assert "ERROR: database connection failed" in rendered
    assert "Role: 시스템 로그 분석 전문가" in rendered


def test_render_plan_template() -> None:
    loader = PromptLoader()

    rendered = loader.render_plan(analysis_report="## Analysis\n- Critical DB timeout")

    assert "## Analysis" in rendered
    assert "Role: 소프트웨어 개선 기획자" in rendered


def test_render_fix_template() -> None:
    loader = PromptLoader()

    rendered = loader.render_fix(
        improvement_plan="1) retry 정책 추가",
        source_code="def run():\n    pass\n",
    )

    assert "retry 정책 추가" in rendered
    assert "def run():" in rendered
    assert "Role: 시니어 소프트웨어 개발자" in rendered


def test_render_review_template() -> None:
    loader = PromptLoader()

    rendered = loader.render_review(
        original_code="def calc(x):\n    return x\n",
        fixed_code="def calc(x):\n    return x * 2\n",
        improvement_plan="곱셈 로직 보강",
    )

    assert "def calc(x):" in rendered
    assert "return x * 2" in rendered
    assert "곱셈 로직 보강" in rendered
    assert "Role: 시니어 코드 리뷰어" in rendered


def test_missing_template_raises(tmp_path: Path) -> None:
    loader = PromptLoader(templates_dir=str(tmp_path))

    with pytest.raises((FileNotFoundError, TemplateNotFound)):
        _ = loader.render("does_not_exist", value="x")


def test_all_templates_exist() -> None:
    loader = PromptLoader()

    assert loader.render("analyze_prompt", log_content="x")
    assert loader.render("plan_prompt", analysis_report="x")
    assert loader.render("fix_prompt", improvement_plan="x", source_code="x")
    assert loader.render(
        "review_prompt",
        original_code="x",
        fixed_code="x",
        improvement_plan="x",
    )
