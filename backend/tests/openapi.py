"""Проверка ответов по схемам из contracts/openapi.yaml: контракт важнее наших моделей."""

from __future__ import annotations

from functools import cache
from typing import Any

import yaml
from openapi_schema_validator import OAS30Validator, oas30_format_checker

from backend.core.settings import REPO_ROOT


@cache
def _components() -> dict[str, Any]:
    spec = yaml.safe_load((REPO_ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    return spec["components"]


def assert_matches_schema(schema_name: str, data: Any) -> None:
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": _components()}
    OAS30Validator(schema, format_checker=oas30_format_checker).validate(data)
