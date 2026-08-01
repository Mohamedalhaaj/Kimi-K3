"""Tool discovery and execution endpoints.

``POST /api/tools/{tool_id}`` runs a tool and returns the invocation record.
For a deterministic tool this *is* the complete answer — the response never
touches the model, which is what makes the calculator cost zero tokens and
return in milliseconds.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body

from kimi.tools.base import ToolStatus
from kimi.tools.registry import ToolEngine, registry

router = APIRouter(prefix="/tools", tags=["tools"])

_engine = ToolEngine()


@router.get("")
async def list_tools() -> dict[str, Any]:
    """Capability manifest. The UI uses this to render and label tools."""
    return {"tools": registry.describe_all()}


@router.post("/{tool_id}")
async def run_tool(
    tool_id: str,
    arguments: Annotated[dict[str, Any], Body()],
    conversation_id: str | None = None,
    approved: bool = False,
) -> dict[str, Any]:
    invocation = await _engine.execute(
        tool_id, arguments, conversation_id=conversation_id, approved=approved
    )
    spec = registry.get(tool_id)
    payload = invocation.to_payload()
    payload["deterministic"] = bool(spec and spec.deterministic)
    # Explicit and machine-checkable: the acceptance test asserts this is False
    # for the calculator, proving no model call was made.
    payload["model_called"] = bool(
        spec and spec.requires_model_followup and invocation.status is ToolStatus.COMPLETED
    )
    return payload
