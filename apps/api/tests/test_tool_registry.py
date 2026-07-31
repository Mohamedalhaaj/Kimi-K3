"""Registry and engine behaviour — the seam that replaces the prototype's if-chain."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from kimi.tools.base import (
    PermissionLevel,
    Renderer,
    ToolContext,
    ToolFailure,
    ToolOutcome,
    ToolSpec,
    ToolStatus,
)
from kimi.tools.registry import ToolEngine, ToolRegistry, warn


class Inp(BaseModel):
    value: int


class Out(BaseModel):
    doubled: int


async def _double(p: Inp, _c: ToolContext) -> ToolOutcome[Out]:
    return ToolOutcome(value=Out(doubled=p.value * 2))


def spec(**kw: object) -> ToolSpec:
    base = {
        "id": "doubler",
        "name": "Doubler",
        "description": "Doubles a number.",
        "input_model": Inp,
        "output_model": Out,
        "handler": _double,
        "deterministic": True,
        "requires_model_followup": False,
    }
    base.update(kw)
    return ToolSpec(**base)  # type: ignore[arg-type]


@pytest.fixture
def engine() -> tuple[ToolEngine, ToolRegistry]:
    reg = ToolRegistry()
    return ToolEngine(reg), reg


async def test_successful_execution_reports_duration(engine) -> None:
    eng, reg = engine
    reg.register(spec())
    inv = await eng.execute("doubler", {"value": 21})

    assert inv.status is ToolStatus.COMPLETED
    assert inv.result == {"doubled": 42}
    # Tool duration is measured separately from any model timing.
    assert inv.duration_ms >= 0
    assert inv.error is None


async def test_unknown_tool_fails_cleanly(engine) -> None:
    eng, _ = engine
    inv = await eng.execute("nope", {})
    assert inv.status is ToolStatus.FAILED
    assert inv.error is not None
    assert inv.error["code"] == "unknown_tool"


async def test_invalid_arguments_are_reported_readably(engine) -> None:
    eng, reg = engine
    reg.register(spec())
    inv = await eng.execute("doubler", {"value": "not a number"})

    assert inv.status is ToolStatus.FAILED
    assert inv.error is not None
    assert inv.error["code"] == "invalid_arguments"
    # A single sentence, not a pydantic dump.
    assert "\n" not in inv.error["message"]


async def test_warnings_promote_the_status(engine) -> None:
    eng, reg = engine

    async def warned(p: Inp, _c: ToolContext) -> ToolOutcome[Out]:
        return ToolOutcome(
            value=Out(doubled=p.value * 2),
            warnings=[warn("partial", "One source could not be read.")],
        )

    reg.register(spec(handler=warned))
    inv = await eng.execute("doubler", {"value": 1})

    assert inv.status is ToolStatus.COMPLETED_WITH_WARNINGS
    assert inv.result == {"doubled": 2}  # the result still stands
    assert inv.warnings[0].code == "partial"


async def test_timeout_is_enforced_and_named(engine) -> None:
    eng, reg = engine

    async def slow(_p: Inp, _c: ToolContext) -> ToolOutcome[Out]:
        await asyncio.sleep(5)
        return ToolOutcome(value=Out(doubled=0))

    reg.register(spec(handler=slow, timeout_s=0.15))
    inv = await eng.execute("doubler", {"value": 1})

    assert inv.status is ToolStatus.FAILED
    assert inv.error is not None
    assert inv.error["code"] == "tool_timeout"
    assert inv.duration_ms < 2000


async def test_tool_failure_message_is_used_verbatim(engine) -> None:
    eng, reg = engine

    async def failing(_p: Inp, _c: ToolContext) -> ToolOutcome[Out]:
        raise ToolFailure("division_by_zero", "Cannot divide by zero.")

    reg.register(spec(handler=failing))
    inv = await eng.execute("doubler", {"value": 1})

    assert inv.status is ToolStatus.FAILED
    assert inv.error == {"code": "division_by_zero", "message": "Cannot divide by zero."}


async def test_unexpected_exception_never_leaks_internals(engine) -> None:
    eng, reg = engine

    async def leaky(_p: Inp, _c: ToolContext) -> ToolOutcome[Out]:
        raise RuntimeError("postgres://user:hunter2@10.0.0.5/internal")

    reg.register(spec(handler=leaky))
    inv = await eng.execute("doubler", {"value": 1})

    assert inv.status is ToolStatus.FAILED
    assert inv.error is not None
    assert "hunter2" not in str(inv.error)
    assert "10.0.0.5" not in str(inv.error)


async def test_approval_gate_blocks_before_execution(engine) -> None:
    eng, reg = engine
    ran = False

    async def sensitive(_p: Inp, _c: ToolContext) -> ToolOutcome[Out]:
        nonlocal ran
        ran = True
        return ToolOutcome(value=Out(doubled=0))

    reg.register(
        spec(
            handler=sensitive,
            permission=PermissionLevel.CONSEQUENTIAL,
            requires_approval=True,
        )
    )

    inv = await eng.execute("doubler", {"value": 1})
    assert inv.status is ToolStatus.WAITING_FOR_APPROVAL
    assert ran is False, "a consequential tool must not run before approval"

    approved = await eng.execute("doubler", {"value": 1}, approved=True)
    assert approved.status is ToolStatus.COMPLETED
    assert ran is True


def test_registry_rejects_contradictory_specs() -> None:
    reg = ToolRegistry()
    # Deterministic but still wanting a model follow-up is exactly how the
    # prototype ended up calling the model after a completed browser action.
    with pytest.raises(ValueError, match="deterministic"):
        reg.register(spec(deterministic=True, requires_model_followup=True))

    with pytest.raises(ValueError, match="approval"):
        reg.register(spec(permission=PermissionLevel.CONSEQUENTIAL, requires_approval=False))


def test_registry_rejects_duplicate_ids() -> None:
    reg = ToolRegistry()
    reg.register(spec())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(spec())


def test_specs_expose_schemas_for_the_ui_and_the_model() -> None:
    reg = ToolRegistry()
    s = reg.register(spec())

    described = s.describe()
    assert described["deterministic"] is True
    assert "properties" in described["input_schema"]
    assert "properties" in described["output_schema"]

    fn = s.json_schema()
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "doubler"


def test_status_terminality() -> None:
    assert ToolStatus.COMPLETED.is_terminal
    assert ToolStatus.FAILED.is_terminal
    assert ToolStatus.CANCELLED.is_terminal
    assert ToolStatus.COMPLETED_WITH_WARNINGS.is_terminal
    assert not ToolStatus.RUNNING.is_terminal
    assert not ToolStatus.QUEUED.is_terminal
    assert not ToolStatus.WAITING_FOR_APPROVAL.is_terminal


async def test_renderer_switches_to_error_renderer_on_failure(engine) -> None:
    eng, reg = engine

    async def failing(_p: Inp, _c: ToolContext) -> ToolOutcome[Out]:
        raise ToolFailure("x", "nope")

    reg.register(spec(handler=failing, renderer=Renderer.SOURCES, error_renderer=Renderer.TEXT))
    inv = await eng.execute("doubler", {"value": 1})
    assert inv.renderer is Renderer.TEXT
