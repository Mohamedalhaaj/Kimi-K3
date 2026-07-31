"""Safe arithmetic.

Ported from ``legacy_streamlit/core/calculator.py``, which the audit judged the
only module with no structural defect: it parses with ``ast.parse(mode="eval")``
and walks an allowlist, so there is no ``eval``, no attribute access and no
subscripting. Two contained bugs are fixed here.

**Fix 1 — the nested-exponent hang (AUDIT §5, calculator.py:50).**
The original guard rejected an exponent above 100 by inspecting only the *right*
operand of each individual ``Pow`` node. Because the tree evaluates bottom-up,
``(((10**99)**99)**99)`` passes every check individually — each right operand is
99 — while the intermediate values grow past any memory limit. At 24 characters
it sat well inside the 300-character cap and hung the single Streamlit process
for every session. The guard is now on the *magnitude of the result*, checked
after every operation, plus a node budget and a wall-clock deadline.

**Fix 2 — booleans (AUDIT §8).**
``isinstance(True, int)`` is true in Python, so ``True + 1`` evaluated to 2.
Booleans are now rejected at the constant.
"""

from __future__ import annotations

import ast
import math
import operator
import time
from collections.abc import Callable
from typing import Any, Final

from pydantic import BaseModel, Field

from kimi.tools.base import (
    PermissionLevel,
    Renderer,
    ToolContext,
    ToolFailure,
    ToolOutcome,
    ToolSpec,
)

Number = int | float

_BINARY: Final[dict[type[ast.operator], Callable[[Any, Any], Any]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: Final[dict[type[ast.unaryop], Callable[[Any], Any]]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_CONSTANTS: Final[dict[str, float]] = {"pi": math.pi, "e": math.e, "tau": math.tau}
_FUNCTIONS: Final[dict[str, Callable[..., Any]]] = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
}

MAX_EXPRESSION_CHARS: Final = 300
#: Results beyond this magnitude are refused. 1e308 is near the float ceiling;
#: this leaves headroom while stopping integer blow-up dead.
MAX_MAGNITUDE: Final = 1e100
MAX_NODES: Final = 200
MAX_SECONDS: Final = 1.0


class CalculatorInput(BaseModel):
    expression: str = Field(
        max_length=MAX_EXPRESSION_CHARS,
        description="An arithmetic expression, e.g. '25*4' or 'sqrt(2)*pi'.",
    )


class CalculatorOutput(BaseModel):
    expression: str
    result: str


class _Evaluator:
    def __init__(self) -> None:
        self.nodes = 0
        self.deadline = time.perf_counter() + MAX_SECONDS

    def _check_budget(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ToolFailure("expression_too_complex", "That expression is too complex.")
        if time.perf_counter() > self.deadline:
            raise ToolFailure("expression_too_slow", "That expression took too long to evaluate.")

    def _guard(self, value: Any) -> Number:
        """Reject anything that is not a finite, bounded real number.

        This is the load-bearing fix: it runs after *every* operation, so an
        intermediate value cannot grow unbounded between guarded nodes.
        """
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ToolFailure("unsupported_value", "That expression is not supported.")
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ToolFailure("result_out_of_range", "The result is not a finite number.")
        # bit_length is O(1) and avoids materialising a huge decimal string.
        if isinstance(value, int) and value.bit_length() > 512:
            raise ToolFailure("result_out_of_range", "The result is too large to calculate.")
        if abs(value) > MAX_MAGNITUDE:
            raise ToolFailure("result_out_of_range", "The result is too large to calculate.")
        return value

    def visit(self, node: ast.AST) -> Number:
        self._check_budget()

        if isinstance(node, ast.Expression):
            return self.visit(node.body)

        if isinstance(node, ast.Constant):
            # bool is a subclass of int; reject it explicitly.
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise ToolFailure("unsupported_value", "Only numbers are supported.")
            return self._guard(node.value)

        if isinstance(node, ast.Name):
            if node.id not in _CONSTANTS:
                raise ToolFailure("unknown_name", f"{node.id!r} is not a known constant.")
            return _CONSTANTS[node.id]

        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left = self.visit(node.left)
            right = self.visit(node.right)
            if isinstance(node.op, ast.Pow):
                # Cheap pre-check so we never *start* an impossible power.
                if abs(right) > 64:
                    raise ToolFailure("result_out_of_range", "That exponent is too large.")
                if left != 0 and abs(right) * math.log10(abs(left) or 1) > 100:
                    raise ToolFailure(
                        "result_out_of_range", "The result is too large to calculate."
                    )
            try:
                value = _BINARY[type(node.op)](left, right)
            except ZeroDivisionError as exc:
                raise ToolFailure("division_by_zero", "Cannot divide by zero.") from exc
            except (OverflowError, ValueError) as exc:
                raise ToolFailure("result_out_of_range", "The result is out of range.") from exc
            return self._guard(value)

        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return self._guard(_UNARY[type(node.op)](self.visit(node.operand)))

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = _FUNCTIONS.get(node.func.id)
            if function is None or node.keywords:
                raise ToolFailure("unsupported_function", "That function is not supported.")
            if len(node.args) > 2:
                raise ToolFailure("unsupported_function", "Too many arguments.")
            args = [self.visit(a) for a in node.args]
            try:
                return self._guard(function(*args))
            except ToolFailure:
                raise
            except (ValueError, OverflowError, TypeError) as exc:
                raise ToolFailure(
                    "math_error", f"{node.func.id}() could not be evaluated with those inputs."
                ) from exc

        raise ToolFailure("unsupported_expression", "That expression is not supported.")


def calculate(expression: str) -> str:
    """Evaluate ``expression`` and format the result."""
    expression = expression.strip()
    if not expression:
        raise ToolFailure("empty_expression", "No expression was provided.")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ToolFailure("expression_too_long", "That expression is too long.")

    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, MemoryError) as exc:
        raise ToolFailure("invalid_syntax", "That is not a valid arithmetic expression.") from exc

    result = _Evaluator().visit(tree)
    if isinstance(result, float):
        if result.is_integer() and abs(result) < 1e15:
            return str(int(result))
        return f"{result:.12g}"
    return str(result)


async def _handler(
    payload: CalculatorInput, _context: ToolContext
) -> ToolOutcome[CalculatorOutput]:
    return ToolOutcome(
        value=CalculatorOutput(
            expression=payload.expression.strip(),
            result=calculate(payload.expression),
        )
    )


CALCULATOR = ToolSpec(
    id="calculator",
    name="Calculator",
    description=(
        "Evaluate an arithmetic expression. Supports + - * / // % **, parentheses, "
        "the constants pi/e/tau, and abs, round, sqrt, sin, cos, tan, log, log10, "
        "ceil, floor."
    ),
    input_model=CalculatorInput,
    output_model=CalculatorOutput,
    handler=_handler,
    # The number IS the answer. No model call, no tokens.
    deterministic=True,
    requires_model_followup=False,
    timeout_s=5.0,
    permission=PermissionLevel.SAFE,
    renderer=Renderer.CALCULATION,
    audit_event="tool.calculator",
)
