"""Provider-agnostic chat interface.

A provider turns a list of messages into a stream of :class:`StreamEvent`.
Everything above this layer (routers, services, the UI) is written against
these types only, so adding another OpenAI-compatible provider later means
adding one file — not rewriting the app.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


class Capability(StrEnum):
    TEXT = "text"
    VISION = "vision"
    TOOLS = "tools"


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """What a model can actually do.

    Used to refuse impossible requests *before* they are sent — the app must
    never ship an image to a text-only model and must never advertise a
    capability the selected model lacks.
    """

    id: str
    label: str
    capabilities: frozenset[Capability]
    context_window: int
    #: None when the operator has not configured pricing; cost is then hidden.
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities


@dataclass(slots=True)
class ImagePart:
    """An inline image. ``data_url`` is a ``data:image/...;base64,`` string."""

    data_url: str
    detail: Literal["auto", "low", "high"] = "auto"


@dataclass(slots=True)
class Message:
    role: Role
    content: str
    images: list[ImagePart] = field(default_factory=list)


# --------------------------------------------------------------------------
# Stream events
# --------------------------------------------------------------------------


@dataclass(slots=True)
class TextDelta:
    """An incremental chunk of assistant text."""

    text: str


@dataclass(slots=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(slots=True)
class Timing:
    """Measured, never estimated. All values in milliseconds."""

    #: Time from request start to the first non-empty text delta.
    first_token_ms: float | None = None
    #: Wall-clock for the whole provider call.
    total_ms: float | None = None


@dataclass(slots=True)
class StreamDone:
    finish_reason: str | None
    usage: Usage
    timing: Timing


StreamEvent = TextDelta | StreamDone


@runtime_checkable
class ChatProvider(Protocol):
    """The single seam between this app and any model vendor."""

    name: str

    def get_model(self, model_id: str) -> ModelInfo:
        """Return capability metadata for ``model_id``."""
        ...

    def list_models(self) -> Sequence[ModelInfo]:
        ...

    def stream_chat(
        self,
        *,
        messages: Sequence[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion.

        Implementations must honour cancellation: when the consumer stops
        iterating (or the task is cancelled) the underlying HTTP request is
        torn down rather than left running.
        """
        ...
