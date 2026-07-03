from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EvalType(StrEnum):
    """Supported deterministic benchmark validators."""

    CONTAINS_ALL = "contains_all"
    EXACT_MATCH = "exact_match"
    JSON_FIELD_EQUALS = "json_field_equals"
    JSON_KEYS = "json_keys"


@dataclass(frozen=True)
class EvalSpec:
    """Deterministic expectation for one workload item."""

    eval_type: EvalType
    expected: str | None = None
    expected_fields: dict[str, str] | None = None
    required: list[str] | None = None
    case_sensitive: bool = False


@dataclass(frozen=True)
class EvalResult:
    """Quality result for one provider response."""

    passed: bool
    score: float
    eval_type: str
    reason: str


def parse_eval_spec(raw: Any) -> EvalSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("eval must be an object")

    eval_type_raw = raw.get("type")
    if not isinstance(eval_type_raw, str):
        raise ValueError("eval.type must be a string")
    eval_type = EvalType(eval_type_raw)

    expected = raw.get("expected")
    if expected is not None and not isinstance(expected, str):
        raise ValueError("eval.expected must be a string")

    expected_fields_raw = raw.get("expected_fields")
    expected_fields: dict[str, str] | None = None
    if expected_fields_raw is not None:
        if not isinstance(expected_fields_raw, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in expected_fields_raw.items()
        ):
            raise ValueError("eval.expected_fields must be an object of strings")
        expected_fields = expected_fields_raw

    required_raw = raw.get("required")
    required: list[str] | None = None
    if required_raw is not None:
        if not isinstance(required_raw, list) or not all(
            isinstance(item, str) for item in required_raw
        ):
            raise ValueError("eval.required must be a list of strings")
        required = required_raw

    case_sensitive = raw.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        raise ValueError("eval.case_sensitive must be a boolean")

    return EvalSpec(
        eval_type=eval_type,
        expected=expected,
        expected_fields=expected_fields,
        required=required,
        case_sensitive=case_sensitive,
    )


def evaluate_text(text: str, spec: EvalSpec | None) -> EvalResult | None:
    if spec is None:
        return None

    if spec.eval_type == EvalType.EXACT_MATCH:
        return _evaluate_exact_match(text, spec)
    if spec.eval_type == EvalType.CONTAINS_ALL:
        return _evaluate_contains_all(text, spec)
    if spec.eval_type == EvalType.JSON_FIELD_EQUALS:
        return _evaluate_json_field_equals(text, spec)
    if spec.eval_type == EvalType.JSON_KEYS:
        return _evaluate_json_keys(text, spec)

    raise ValueError(f"Unsupported eval type: {spec.eval_type}")


def _evaluate_exact_match(text: str, spec: EvalSpec) -> EvalResult:
    expected = spec.expected
    if expected is None:
        raise ValueError("exact_match eval requires expected")
    actual = text.strip()
    expected_value = expected.strip()
    if not spec.case_sensitive:
        actual = actual.lower()
        expected_value = expected_value.lower()
    passed = actual == expected_value
    return EvalResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        eval_type=spec.eval_type.value,
        reason="exact match passed" if passed else "response did not exactly match expected text",
    )


def _evaluate_contains_all(text: str, spec: EvalSpec) -> EvalResult:
    required = spec.required
    if not required:
        raise ValueError("contains_all eval requires required")
    haystack = text if spec.case_sensitive else text.lower()
    missing = [
        item
        for item in required
        if (item if spec.case_sensitive else item.lower()) not in haystack
    ]
    passed = not missing
    score = (len(required) - len(missing)) / len(required)
    return EvalResult(
        passed=passed,
        score=score,
        eval_type=spec.eval_type.value,
        reason="all required substrings present"
        if passed
        else f"missing required substrings: {', '.join(missing)}",
    )


def _evaluate_json_keys(text: str, spec: EvalSpec) -> EvalResult:
    required = spec.required
    if not required:
        raise ValueError("json_keys eval requires required")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return EvalResult(
            passed=False,
            score=0.0,
            eval_type=spec.eval_type.value,
            reason=f"invalid JSON: {exc.msg}",
        )
    if not isinstance(parsed, dict):
        return EvalResult(
            passed=False,
            score=0.0,
            eval_type=spec.eval_type.value,
            reason="JSON response is not an object",
        )

    missing = [key for key in required if key not in parsed]
    passed = not missing
    score = (len(required) - len(missing)) / len(required)
    return EvalResult(
        passed=passed,
        score=score,
        eval_type=spec.eval_type.value,
        reason="all required JSON keys present" if passed else f"missing JSON keys: {', '.join(missing)}",
    )


def _evaluate_json_field_equals(text: str, spec: EvalSpec) -> EvalResult:
    expected_fields = spec.expected_fields
    if not expected_fields:
        raise ValueError("json_field_equals eval requires expected_fields")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return EvalResult(
            passed=False,
            score=0.0,
            eval_type=spec.eval_type.value,
            reason=f"invalid JSON: {exc.msg}",
        )
    if not isinstance(parsed, dict):
        return EvalResult(
            passed=False,
            score=0.0,
            eval_type=spec.eval_type.value,
            reason="JSON response is not an object",
        )

    mismatches: list[str] = []
    matched = 0
    for field, expected in expected_fields.items():
        actual = parsed.get(field)
        if not isinstance(actual, str):
            mismatches.append(field)
            continue
        actual_value = actual.strip()
        expected_value = expected.strip()
        if not spec.case_sensitive:
            actual_value = actual_value.lower()
            expected_value = expected_value.lower()
        if actual_value == expected_value:
            matched += 1
        else:
            mismatches.append(field)

    passed = not mismatches
    score = matched / len(expected_fields)
    return EvalResult(
        passed=passed,
        score=score,
        eval_type=spec.eval_type.value,
        reason="all expected JSON fields matched"
        if passed
        else f"mismatched JSON fields: {', '.join(mismatches)}",
    )
