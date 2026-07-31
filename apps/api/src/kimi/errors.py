"""Structured error taxonomy.

The UI must be able to tell a model failure apart from a search failure, a
timeout apart from a cancellation, and a missing capability apart from a bug.
Every failure raised inside the app is a :class:`KimiError` carrying a stable
machine-readable ``code`` plus a message that is safe to show a user.

Raw exception text is *never* the user-facing message: ``detail`` is for logs
and the developer diagnostics panel only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # provider / model
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_AUTH = "model_auth"
    MODEL_RATE_LIMITED = "model_rate_limited"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_BAD_RESPONSE = "model_bad_response"
    # capability
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    # tools
    TOOL_FAILED = "tool_failed"
    TOOL_TIMEOUT = "tool_timeout"
    SEARCH_FAILED = "search_failed"
    EXTRACTION_FAILED = "extraction_failed"
    BROWSER_FAILED = "browser_failed"
    FILE_PARSE_FAILED = "file_parse_failed"
    # transport / lifecycle
    NETWORK_TIMEOUT = "network_timeout"
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"
    INTERNAL = "internal"


#: HTTP status used when an error surfaces through the REST layer.
_STATUS: dict[ErrorCode, int] = {
    ErrorCode.MODEL_AUTH: 502,
    ErrorCode.MODEL_UNAVAILABLE: 502,
    ErrorCode.MODEL_BAD_RESPONSE: 502,
    ErrorCode.MODEL_RATE_LIMITED: 429,
    ErrorCode.MODEL_TIMEOUT: 504,
    ErrorCode.NETWORK_TIMEOUT: 504,
    ErrorCode.TOOL_TIMEOUT: 504,
    ErrorCode.UNSUPPORTED_CAPABILITY: 422,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CANCELLED: 499,
}


class KimiError(Exception):
    """Base class for every deliberate failure in the application."""

    code: ErrorCode = ErrorCode.INTERNAL
    #: Shown to the user. Must never contain a raw exception or a secret.
    user_message: str = "Something went wrong."
    #: Whether retrying the same request could plausibly succeed. This belongs
    #: to the error *type* — a rate limit is always worth retrying and a bad API
    #: key never is — so call sites cannot forget to set it and leave the UI
    #: unable to offer a retry.
    default_retryable: bool = False

    def __init__(
        self,
        user_message: str | None = None,
        *,
        detail: str | None = None,
        retryable: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.user_message = user_message or self.user_message
        self.detail = detail
        self.retryable = self.default_retryable if retryable is None else retryable
        self.context = context or {}
        super().__init__(self.user_message)

    @property
    def http_status(self) -> int:
        return _STATUS.get(self.code, 500)

    def to_payload(self, *, include_detail: bool) -> dict[str, Any]:
        """Serialise for the wire. ``include_detail`` is debug-mode only."""
        payload: dict[str, Any] = {
            "code": str(self.code),
            "message": self.user_message,
            "retryable": self.retryable,
        }
        if self.context:
            payload["context"] = self.context
        if include_detail and self.detail:
            payload["detail"] = self.detail
        return payload


class ModelError(KimiError):
    code = ErrorCode.MODEL_UNAVAILABLE
    user_message = "The AI model could not be reached. Please try again."
    default_retryable = True


class ModelAuthError(ModelError):
    code = ErrorCode.MODEL_AUTH
    user_message = (
        "The model provider rejected the API key. Check TOKENROUTER_API_KEY in your .env."
    )
    # A bad credential will fail identically every time.
    default_retryable = False


class ModelRateLimitedError(ModelError):
    code = ErrorCode.MODEL_RATE_LIMITED
    user_message = "The model provider is rate limiting requests. Try again shortly."
    default_retryable = True


class ModelTimeoutError(ModelError):
    code = ErrorCode.MODEL_TIMEOUT
    user_message = "The model took too long to respond."
    default_retryable = True


class ModelBadResponseError(ModelError):
    code = ErrorCode.MODEL_BAD_RESPONSE
    user_message = "The model returned a response this app could not read."
    default_retryable = True


class UnsupportedCapabilityError(KimiError):
    code = ErrorCode.UNSUPPORTED_CAPABILITY
    user_message = "The selected model does not support this."


class CancelledError(KimiError):
    code = ErrorCode.CANCELLED
    user_message = "Generation stopped."


class NotFoundError(KimiError):
    code = ErrorCode.NOT_FOUND
    user_message = "Not found."


class InvalidRequestError(KimiError):
    code = ErrorCode.INVALID_REQUEST
    user_message = "That request was not valid."
