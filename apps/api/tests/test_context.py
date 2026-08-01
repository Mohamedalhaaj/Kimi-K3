"""Context-budget tests.

AUDIT §5 (app.py:339-341): the prototype bounded the prompt by message count
only, so twelve Deep turns re-sent up to 660,000 characters of stale text.
"""

from __future__ import annotations

from kimi.db.base import Message as DbMessage
from kimi.services.context import (
    PRESETS,
    build_context,
    estimate_tokens,
)


def msg(role: str, content: str, seq: int = 0) -> DbMessage:
    return DbMessage(conversation_id="c", seq=seq, role=role, content=content)


def test_estimate_is_conservative_for_dense_scripts() -> None:
    # Arabic packs more tokens per character than English; the estimator must
    # not under-count it, or the budget silently overflows.
    assert estimate_tokens("الأخبار الليبية اليوم") > 0
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 260) >= 100


def test_history_is_bounded_by_tokens_not_just_message_count() -> None:
    huge = "x" * 200_000  # a scraped page, as the prototype would have re-sent
    history = [msg("user", huge, i) for i in range(10)]
    built = build_context(
        history=history,
        system_prompt="sys",
        context_window=8_000,
        mode="balanced",
        supports_vision=False,
    )
    assert built.report.truncated is True
    assert built.report.estimated_prompt_tokens <= built.report.budget_tokens
    assert built.report.dropped_messages > 0


def test_newest_turns_always_survive() -> None:
    history = [msg("user", "x" * 5_000, i) for i in range(20)]
    history.append(msg("user", "the actual question", 20))
    built = build_context(
        history=history,
        system_prompt="sys",
        context_window=4_000,
        mode="fast",
        supports_vision=False,
    )
    assert built.messages[-1].content == "the actual question"


def test_system_prompt_is_always_first() -> None:
    built = build_context(
        history=[msg("user", "hi", 0)],
        system_prompt="SYSTEM",
        context_window=128_000,
        mode="balanced",
        supports_vision=False,
    )
    assert built.messages[0].role == "system"
    assert built.messages[0].content == "SYSTEM"


def test_images_dropped_for_text_only_model_are_counted() -> None:
    built = build_context(
        history=[msg("user", "look", 0)],
        system_prompt="sys",
        context_window=128_000,
        mode="balanced",
        supports_vision=False,
        pending_images=["data:image/png;base64,a", "data:image/png;base64,b"],
    )
    assert built.report.dropped_images == 2
    assert all(not m.images for m in built.messages)


def test_images_attach_to_the_last_user_turn_for_vision_models() -> None:
    built = build_context(
        history=[msg("user", "one", 0), msg("assistant", "ok", 1), msg("user", "two", 2)],
        system_prompt="sys",
        context_window=128_000,
        mode="balanced",
        supports_vision=True,
        pending_images=["data:image/png;base64,a"],
    )
    carriers = [m for m in built.messages if m.images]
    assert len(carriers) == 1
    assert carriers[0].content == "two"
    assert built.report.dropped_images == 0


def test_presets_differ_in_more_than_max_tokens() -> None:
    fast, balanced, deep = PRESETS["fast"], PRESETS["balanced"], PRESETS["deep"]
    assert fast.max_output_tokens < balanced.max_output_tokens < deep.max_output_tokens
    assert fast.history_budget_ratio < deep.history_budget_ratio
    assert fast.max_turns < deep.max_turns
    assert fast.temperature != balanced.temperature


def test_report_totals_are_self_consistent() -> None:
    history = [msg("user", f"turn {i}", i) for i in range(6)]
    built = build_context(
        history=history,
        system_prompt="sys",
        context_window=128_000,
        mode="balanced",
        supports_vision=False,
    )
    # +1 for the system message.
    assert len(built.messages) == built.report.included_messages + 1
    assert built.report.dropped_messages == 0
    assert built.report.truncated is False
