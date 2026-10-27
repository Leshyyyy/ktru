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
    return _allow_null_in_nullable_enums(spec["components"])


def _allow_null_in_nullable_enums(node: Any) -> Any:
    """`nullable: true` вместе с `enum` или `allOf` валидатор OpenAPI 3.0 понимает строго: null проверяется
    по enum и по вложенной схеме объекта. По смыслу контракта null там разрешён (error_code, band, path,
    extracted, final, layout_hint; см. пример Job) — так и проверяем: null либо значение по схеме.
    """
    if isinstance(node, dict):
        if node.get("nullable") is True and "enum" in node and None not in node["enum"]:
            node["enum"] = [*node["enum"], None]
        if node.get("nullable") is True and "allOf" in node:
            node["anyOf"] = [{"allOf": node.pop("allOf")}, {"enum": [None]}]
        for value in node.values():
            _allow_null_in_nullable_enums(value)
    elif isinstance(node, list):
        for value in node:
            _allow_null_in_nullable_enums(value)
    return node


def assert_matches_schema(schema_name: str, data: Any) -> None:
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": _components()}
    OAS30Validator(schema, format_checker=oas30_format_checker).validate(data)
