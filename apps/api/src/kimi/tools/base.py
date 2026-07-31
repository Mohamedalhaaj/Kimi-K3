"""The tool contract.

The audit's root-cause finding (docs/AUDIT.md §7) was *patch-the-symbol instead
of change-the-seam*: with no registry and no interface, the prototype added its
third tool by mutating module attributes from a package ``__init__`` and its
LLM bypass by mutating an SDK class at runtime.

This module is that missing seam. A tool is a :class:`ToolSpec` — a declaration
of everything the engine and the UI need to know — plus one async handler. There
is no ``if`` chain anywhere; dispatch is a dictionary lookup in the registry.

The most consequential field is :attr:`ToolSpec.deterministic`. A deterministic
tool's result *is* the answer: the engine returns it directly and never calls the
model. That is what makes `/calc 25*4` cost zero tokens.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ToolStatus(StrEnum):
    """Lifecycle states, mirrored one-to-one in the UI."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_FOR_APPROVAL = "waiting_for_approval"

    @property
    def is_terminal(self) -> bool:
        return self not in (
            ToolStatus.QUEUED,
            ToolStatus.RUNNING,
            ToolStatus.WAITING_FOR_APPROVAL,
        )


class PermissionLevel(StrEnum):
    """How much trust an invocation needs.

    ``READ_PUBLIC`` reaches the public internet. ``LOCAL`` touches this machine.
    ``CONSEQUENTIAL`` changes something outside the app and always requires
    explicit, immediate approval.
    """

    SAFE = "safe"
    READ_PUBLIC = "read_public"
    LOCAL = "local"
    CONSEQUENTIAL = "consequential"


class Renderer(StrEnum):
    """Which frontend component renders this tool's payload."""

    TEXT = "text"
    CALCULATION = "calculation"
    SOURCES = "sources"
    ARTICLE = "article"
    JSON = "json"


@dataclass(slots=True)
class ToolWarning:
    """A non-fatal problem. Presence flips the result to COMPLETED_WITH_WARNINGS."""

    code: str
    message: str


@dataclass(slots=True)
class ToolContext:
    """Per-invocation services and identity."""

    invocation_id: str
    conversation_id: str | None = None
    #: Set when the user cancels; long-running tools should poll it.
    deadline_s: float = 30.0
    started_at: float = field(default_factory=time.perf_counter)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000

    def remaining_s(self) -> float:
        return max(0.0, self.deadline_s - (time.perf_counter() - self.started_at))


@dataclass(slots=True)
class ToolInvocation:
    """The observable record of one tool run.

    ``duration_ms`` is the tool's own wall clock and is reported separately from
    model timing — the brief forbids labelling a deterministic result "0.0s" when
    the tool actually took seconds.
    """

    id: str
    tool_id: str
    status: ToolStatus
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    warnings: list[ToolWarning] = field(default_factory=list)
    error: dict[str, Any] | None = None
    duration_ms: float = 0.0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    renderer: Renderer = Renderer.JSON

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_id": self.tool_id,
            "status": str(self.status),
            "arguments": self.arguments,
            "result": self.result,
            "warnings": [{"code": w.code, "message": w.message} for w in self.warnings],
            "error": self.error,
            "duration_ms": round(self.duration_ms, 1),
            "renderer": str(self.renderer),
            "started_at": self.started_at.isoformat(),
        }


class ToolFailure(Exception):
    """Raised by a handler to fail cleanly with a user-safe message."""

    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


@dataclass(slots=True)
class ToolOutcome[TOut: BaseModel]:
    """What a handler returns: a typed payload plus any warnings."""

    value: TOut
    warnings: list[ToolWarning] = field(default_factory=list)


Handler = Callable[[Any, ToolContext], Awaitable[ToolOutcome[Any]]]


@dataclass(frozen=True, slots=True)
class ToolSpec[TIn: BaseModel, TOut: BaseModel]:
    """Everything the engine and the UI need to know about one tool."""

    id: str
    name: str
    description: str
    input_model: type[TIn]
    output_model: type[TOut]
    handler: Handler

    #: True when the tool's own output is the final answer. The engine returns
    #: it directly and makes no model call.
    deterministic: bool = False
    #: True when the model must see the result and write prose from it.
    requires_model_followup: bool = True

    timeout_s: float = 30.0
    permission: PermissionLevel = PermissionLevel.SAFE
    requires_approval: bool = False
    #: Whether the handler honours cooperative cancellation.
    cancellable: bool = True

    renderer: Renderer = Renderer.JSON
    error_renderer: Renderer = Renderer.TEXT
    #: Emitted to the audit log on every invocation.
    audit_event: str = ""

    def json_schema(self) -> dict[str, Any]:
        """OpenAI-compatible function schema, for model-driven selection."""
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def describe(self) -> dict[str, Any]:
        """Machine-readable capability record, served to the UI."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "deterministic": self.deterministic,
            "requires_model_followup": self.requires_model_followup,
            "requires_approval": self.requires_approval,
            "permission": str(self.permission),
            "timeout_s": self.timeout_s,
            "cancellable": self.cancellable,
            "renderer": str(self.renderer),
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
        }


def new_invocation_id() -> str:
    return uuid.uuid4().hex[:16]
