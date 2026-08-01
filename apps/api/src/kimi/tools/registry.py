"""Tool registry and execution engine.

Dispatch is a dictionary lookup. There is deliberately no ``if tool_id == ...``
chain anywhere in the codebase — that pattern is what the audit identified as the
prototype's root design flaw.

The engine owns the parts every tool would otherwise reimplement badly:
argument validation, timeout, cancellation, duration measurement, warning
promotion, error shaping, and the audit record.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import structlog
from pydantic import ValidationError

from kimi.tools.base import (
    PermissionLevel,
    ToolContext,
    ToolFailure,
    ToolInvocation,
    ToolSpec,
    ToolStatus,
    ToolWarning,
    new_invocation_id,
)

log = structlog.get_logger(__name__)


class ToolRegistry:
    """An immutable-after-startup collection of tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec[Any, Any]] = {}

    def register(self, spec: ToolSpec[Any, Any]) -> ToolSpec[Any, Any]:
        if spec.id in self._tools:
            raise ValueError(f"tool {spec.id!r} is already registered")
        if spec.deterministic and spec.requires_model_followup:
            # A deterministic tool whose result still needs prose is a
            # contradiction, and it is exactly how the prototype ended up
            # sending thousands of tokens after a completed browser click.
            raise ValueError(
                f"tool {spec.id!r} cannot be deterministic and require a model follow-up"
            )
        if spec.permission is PermissionLevel.CONSEQUENTIAL and not spec.requires_approval:
            raise ValueError(f"consequential tool {spec.id!r} must require approval")
        self._tools[spec.id] = spec
        return spec

    def get(self, tool_id: str) -> ToolSpec[Any, Any] | None:
        return self._tools.get(tool_id)

    def __contains__(self, tool_id: object) -> bool:
        return tool_id in self._tools

    def __iter__(self) -> Iterator[ToolSpec[Any, Any]]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def describe_all(self) -> list[dict[str, Any]]:
        return [t.describe() for t in self._tools.values()]

    def json_schemas(self) -> list[dict[str, Any]]:
        return [t.json_schema() for t in self._tools.values()]


#: The process-wide registry. Populated by kimi.tools.builtin at import time.
registry = ToolRegistry()


class ToolEngine:
    """Executes tools with uniform timeout, cancellation and observability."""

    def __init__(self, reg: ToolRegistry | None = None) -> None:
        # `reg or registry` would be wrong: ToolRegistry defines __len__, so an
        # empty registry is falsy and would silently fall back to the global one.
        self._registry = registry if reg is None else reg

    async def execute(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        conversation_id: str | None = None,
        approved: bool = False,
    ) -> ToolInvocation:
        spec = self._registry.get(tool_id)
        invocation_id = new_invocation_id()

        if spec is None:
            return ToolInvocation(
                id=invocation_id,
                tool_id=tool_id,
                status=ToolStatus.FAILED,
                arguments=arguments,
                error={"code": "unknown_tool", "message": f"No tool named {tool_id!r}."},
            )

        invocation = ToolInvocation(
            id=invocation_id,
            tool_id=spec.id,
            status=ToolStatus.QUEUED,
            arguments=arguments,
            renderer=spec.renderer,
        )

        # Approval is checked before any argument work so that a consequential
        # tool cannot cause side effects while "validating".
        if spec.requires_approval and not approved:
            invocation.status = ToolStatus.WAITING_FOR_APPROVAL
            return invocation

        try:
            parsed = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            invocation.status = ToolStatus.FAILED
            invocation.renderer = spec.error_renderer
            invocation.error = {
                "code": "invalid_arguments",
                "message": _first_validation_message(exc),
            }
            return invocation

        context = ToolContext(
            invocation_id=invocation_id,
            conversation_id=conversation_id,
            deadline_s=spec.timeout_s,
        )
        invocation.status = ToolStatus.RUNNING
        started = time.perf_counter()

        try:
            outcome = await asyncio.wait_for(spec.handler(parsed, context), timeout=spec.timeout_s)
            invocation.duration_ms = (time.perf_counter() - started) * 1000
            invocation.result = outcome.value.model_dump(mode="json")
            invocation.warnings = list(outcome.warnings)
            invocation.status = (
                ToolStatus.COMPLETED_WITH_WARNINGS if outcome.warnings else ToolStatus.COMPLETED
            )

        except TimeoutError:
            invocation.duration_ms = (time.perf_counter() - started) * 1000
            invocation.status = ToolStatus.FAILED
            invocation.renderer = spec.error_renderer
            invocation.error = {
                "code": "tool_timeout",
                "message": f"{spec.name} took longer than {spec.timeout_s:.0f}s and was stopped.",
                "retryable": True,
            }

        except asyncio.CancelledError:
            invocation.duration_ms = (time.perf_counter() - started) * 1000
            invocation.status = ToolStatus.CANCELLED
            invocation.error = {"code": "cancelled", "message": "Stopped."}
            raise

        except ToolFailure as exc:
            invocation.duration_ms = (time.perf_counter() - started) * 1000
            invocation.status = ToolStatus.FAILED
            invocation.renderer = spec.error_renderer
            invocation.error = {"code": exc.code, "message": exc.message}
            log.warning(
                "tool.failed",
                tool=spec.id,
                code=exc.code,
                detail=exc.detail,
                invocation_id=invocation_id,
            )

        except Exception as exc:
            invocation.duration_ms = (time.perf_counter() - started) * 1000
            invocation.status = ToolStatus.FAILED
            invocation.renderer = spec.error_renderer
            # The exception message may contain internal hosts or paths.
            invocation.error = {
                "code": "tool_failed",
                "message": f"{spec.name} could not complete.",
                "retryable": True,
            }
            log.error(
                "tool.unhandled",
                tool=spec.id,
                exc_type=type(exc).__name__,
                invocation_id=invocation_id,
            )

        log.info(
            spec.audit_event or f"tool.{spec.id}",
            tool=spec.id,
            invocation_id=invocation_id,
            status=str(invocation.status),
            duration_ms=round(invocation.duration_ms, 1),
            conversation_id=conversation_id,
            warnings=len(invocation.warnings),
        )
        return invocation


def _first_validation_message(exc: ValidationError) -> str:
    """A single readable sentence, never the raw pydantic dump."""
    errors = exc.errors()
    if not errors:
        return "Those arguments were not valid."
    first = errors[0]
    location = ".".join(str(p) for p in first.get("loc", ())) or "input"
    return f"{location}: {first.get('msg', 'invalid value')}"


def warn(code: str, message: str) -> ToolWarning:
    return ToolWarning(code=code, message=message)
