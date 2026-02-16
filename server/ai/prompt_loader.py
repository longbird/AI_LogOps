from __future__ import annotations

import logging
import importlib
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

from shared.utils import setup_logging


class _Template(Protocol):
    def render(self, **variables: str) -> str: ...


class _Environment(Protocol):
    def get_template(self, name: str) -> _Template: ...


class _EnvironmentConstructor(Protocol):
    def __call__(self, *, loader: object, autoescape: bool) -> _Environment: ...


class _LoaderConstructor(Protocol):
    def __call__(self, searchpath: str) -> object: ...


_jinja2: ModuleType = importlib.import_module("jinja2")
_environment_constructor: _EnvironmentConstructor = cast(
    _EnvironmentConstructor,
    getattr(_jinja2, "Environment"),
)
_loader_constructor: _LoaderConstructor = cast(
    _LoaderConstructor,
    getattr(_jinja2, "FileSystemLoader"),
)


class PromptLoader:
    def __init__(self, templates_dir: str | None = None) -> None:
        default_templates_dir = Path(__file__).resolve().parent / "prompts"
        resolved_templates_dir = (
            Path(templates_dir) if templates_dir else default_templates_dir
        )

        self._templates_dir: Path = resolved_templates_dir
        self._environment: _Environment = _environment_constructor(
            loader=_loader_constructor(str(self._templates_dir)),
            autoescape=False,
        )
        self._logger: logging.Logger = setup_logging(self.__class__.__name__)

    def render(self, template_name: str, **variables: str) -> str:
        normalized_name = (
            template_name if template_name.endswith(".j2") else f"{template_name}.j2"
        )
        template = self._environment.get_template(normalized_name)
        rendered = template.render(**variables)
        self._logger.debug("rendered template: %s", normalized_name)
        return rendered

    def render_analyze(self, log_content: str) -> str:
        return self.render("analyze_prompt", log_content=log_content)

    def render_plan(self, analysis_report: str) -> str:
        return self.render("plan_prompt", analysis_report=analysis_report)

    def render_fix(self, improvement_plan: str, source_code: str) -> str:
        return self.render(
            "fix_prompt",
            improvement_plan=improvement_plan,
            source_code=source_code,
        )

    def render_review(
        self,
        original_code: str,
        fixed_code: str,
        improvement_plan: str,
    ) -> str:
        return self.render(
            "review_prompt",
            original_code=original_code,
            fixed_code=fixed_code,
            improvement_plan=improvement_plan,
        )
