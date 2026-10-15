"""Small shared validation primitives for untrusted configuration and numeric budgets."""

from __future__ import annotations

import math
from typing import Any


def number(value: Any, where: str, *, minimum: float = 0, positive: bool = False) -> float:
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < minimum or (positive and value == minimum):
        relation = "greater than" if positive else "at least"
        raise ValueError(f"{where} must be a finite number {relation} {minimum:g}")
    return float(value)


def integer(value: Any, where: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{where} must be an integer from {minimum} to {maximum if maximum is not None else 'unbounded'}")
    return value


def text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a nonempty string")
    return value


def table(value: Any, where: str, keys: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a table")
    if keys is not None:
        unknown = value.keys() - keys
        if unknown:
            raise ValueError(f"{where}: unknown field(s): {', '.join(sorted(unknown))}")
    return value


def array(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be an array")
    return value
