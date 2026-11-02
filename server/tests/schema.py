"""Минимальный валидатор JSON Schema для контрактных тестов (без внешних зависимостей).

Поддерживает подмножество, которое используется в contracts/: $ref, type, const, enum,
required, properties, additionalProperties, items, oneOf, minimum, maximum,
minLength, maxLength, pattern.
"""

import json
import re
from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load_openapi() -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))


def load_ws_schema() -> dict[str, Any]:
    return json.loads((CONTRACTS / "ws-messages.schema.json").read_text(encoding="utf-8"))


def _resolve(root: dict[str, Any], ref: str) -> dict[str, Any]:
    assert ref.startswith("#/"), f"поддерживаются только локальные $ref: {ref}"
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def errors(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> list[str]:
    if "$ref" in schema:
        return errors(value, _resolve(root, schema["$ref"]), root, path)
    out: list[str] = []
    if "oneOf" in schema:
        matches = sum(1 for s in schema["oneOf"] if not errors(value, s, root, path))
        if matches != 1:
            out.append(f"{path}: oneOf совпало {matches} вариантов")
        return out
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            return [f"{path}: ожидался тип {t}, получено {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: ожидалось {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} не из {schema['enum']}")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: меньше {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: больше {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: короче {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: длиннее {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: не соответствует {schema['pattern']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}: нет обязательного поля {key!r}")
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                out += errors(item, props[key], root, f"{path}.{key}")
            elif extra is False:
                out.append(f"{path}: лишнее поле {key!r}")
            elif isinstance(extra, dict):
                out += errors(item, extra, root, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += errors(item, schema["items"], root, f"{path}[{i}]")
    return out


def assert_valid(value: Any, schema: dict[str, Any], root: dict[str, Any]) -> None:
    errs = errors(value, schema, root)
    assert not errs, "нарушение контракта:\n  " + "\n  ".join(errs)
