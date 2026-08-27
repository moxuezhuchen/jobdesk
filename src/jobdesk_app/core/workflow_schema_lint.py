"""Small dependency-free JSON Schema structural linter for verified schemas.

It is intentionally not an admission validator.  It gives a base JobDesk
installation useful local feedback while the producer's machine contract and
remote validation remain authoritative.
"""

from __future__ import annotations

from typing import Any


def lint_workflow_schema(instance: Any, schema: dict[str, Any] | None) -> list[str]:
    """Return deterministic structural errors for the portable schema subset."""
    if schema is None:
        return []
    errors: list[str] = []

    def visit(value: Any, rule: dict[str, Any], path: str) -> None:
        expected = rule.get("type")
        if expected == "object" and not isinstance(value, dict):
            errors.append(f"{path}: expected object")
            return
        if expected == "array" and not isinstance(value, list):
            errors.append(f"{path}: expected array")
            return
        if expected == "string" and not isinstance(value, str):
            errors.append(f"{path}: expected string")
            return
        if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            errors.append(f"{path}: expected integer")
            return
        if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            errors.append(f"{path}: expected number")
            return
        enum = rule.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path}: value is not one of the permitted values")
        if isinstance(value, dict):
            properties_value = rule.get("properties")
            properties: dict[str, Any] = properties_value if isinstance(properties_value, dict) else {}
            required_value = rule.get("required")
            required: list[Any] = required_value if isinstance(required_value, list) else []
            for key in required:
                if key not in value:
                    errors.append(f"{path}.{key}: required property is missing")
            if rule.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{path}.{key}: unknown property")
            for key, child in properties.items():
                if key in value and isinstance(child, dict):
                    visit(value[key], child, f"{path}.{key}")
        if isinstance(value, list):
            minimum = rule.get("minItems")
            if isinstance(minimum, int) and len(value) < minimum:
                errors.append(f"{path}: expected at least {minimum} item(s)")
            item_rule = rule.get("items")
            if isinstance(item_rule, dict):
                for index, item in enumerate(value):
                    visit(item, item_rule, f"{path}[{index}]")

    visit(instance, schema, "$")
    return errors
