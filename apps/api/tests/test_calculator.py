"""Calculator tests, including the two defects the audit found in the original."""

from __future__ import annotations

import time

import pytest

from kimi.tools.base import ToolFailure
from kimi.tools.calculator import calculate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("25*4", "100"),
        ("2+3*4", "14"),
        ("(2+3)*4", "20"),
        ("10/4", "2.5"),
        ("10//4", "2"),
        ("10%3", "1"),
        ("2**10", "1024"),
        ("-5+3", "-2"),
        ("abs(-7)", "7"),
        ("round(3.14159, 2)", "3.14"),
        ("sqrt(16)", "4"),
        ("floor(3.9)", "3"),
        ("ceil(3.1)", "4"),
        ("log10(1000)", "3"),
    ],
)
def test_basic_arithmetic(expression: str, expected: str) -> None:
    assert calculate(expression) == expected


def test_constants() -> None:
    assert calculate("pi").startswith("3.14159")
    assert calculate("e").startswith("2.71828")


def test_nested_exponent_dos_is_refused_quickly() -> None:
    """AUDIT §5 (calculator.py:50): the original hung the whole process here.

    The old guard checked only the right operand of each Pow node, so every
    exponent of 99 passed while the intermediate values exploded.
    """
    started = time.perf_counter()
    with pytest.raises(ToolFailure) as exc:
        calculate("(((10**99)**99)**99)")
    elapsed = time.perf_counter() - started

    assert exc.value.code == "result_out_of_range"
    # The point is that it *returns* — and fast.
    assert elapsed < 1.0


@pytest.mark.parametrize(
    "expression",
    [
        "10**1000",
        "(10**50)**50",
        "((2**60)**60)**60",
        "9**9**9",
    ],
)
def test_large_powers_are_refused(expression: str) -> None:
    started = time.perf_counter()
    with pytest.raises(ToolFailure):
        calculate(expression)
    assert time.perf_counter() - started < 1.0


def test_booleans_are_rejected() -> None:
    """AUDIT §8: isinstance(True, int) let booleans through as 1/0."""
    with pytest.raises(ToolFailure) as exc:
        calculate("True + 1")
    assert exc.value.code in ("unsupported_value", "unknown_name")


def test_division_by_zero_is_named() -> None:
    with pytest.raises(ToolFailure) as exc:
        calculate("1/0")
    assert exc.value.code == "division_by_zero"
    assert "zero" in exc.value.message.lower()


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "().__class__",
        "open('/etc/passwd')",
        "[1,2,3][0]",
        "{'a':1}",
        "lambda: 1",
        "x := 5",
        "exec('1')",
        "os.system('ls')",
        "'a'*100",
    ],
)
def test_non_arithmetic_is_refused(expression: str) -> None:
    with pytest.raises(ToolFailure):
        calculate(expression)


def test_empty_and_overlong_expressions() -> None:
    with pytest.raises(ToolFailure) as empty:
        calculate("   ")
    assert empty.value.code == "empty_expression"

    with pytest.raises(ToolFailure) as long:
        calculate("1+" * 200 + "1")
    assert long.value.code in ("expression_too_long", "expression_too_complex")


def test_deeply_nested_expression_hits_the_node_budget() -> None:
    with pytest.raises(ToolFailure):
        calculate("1" + "+1" * 250)


def test_float_formatting_is_clean() -> None:
    # An integral float should not render as "4.0".
    assert calculate("2.0*2") == "4"
    assert calculate("1/3").startswith("0.333333")
