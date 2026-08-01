"""Browser agent safety.

AUDIT §5 (browser_agent.py:14-28, 95-101, 194, 216-227): the prototype's guards
were English substring denylists checked against the USER'S QUERY, never against
the resolved element or the typed value.
"""

from __future__ import annotations

import pytest

from kimi.browser.safety import (
    Verdict,
    classify_click,
    classify_typed_value,
    redact_field_value,
)
from kimi.tools.registry import ToolEngine, registry

# ---- the value being typed ------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "4111111111111111",  # Visa test number
        "4111 1111 1111 1111",  # spaced
        "5500-0000-0000-0004",  # Mastercard test, dashed
    ],
)
def test_payment_cards_are_refused_by_shape(value: str) -> None:
    """The prototype had no Luhn check and no digit-run heuristic at all."""
    decision = classify_typed_value("Account", value)
    assert decision.verdict is Verdict.REFUSE
    assert "card" in decision.reason.lower()


def test_a_number_that_is_not_a_card_is_allowed() -> None:
    # Right length, fails Luhn: an order or reference number.
    assert classify_typed_value("Order reference", "1234567812345678").verdict is Verdict.ALLOW


@pytest.mark.parametrize("value", ["482913", "0000", "12345678"])
def test_one_time_codes_are_refused(value: str) -> None:
    """`/browser type Email :: 482913` typed an OTP in the prototype."""
    assert classify_typed_value("Email", value).verdict is Verdict.REFUSE


@pytest.mark.parametrize(
    "label",
    [
        "Password",
        "passcode",
        "PIN",
        "OTP",
        "CVV",
        "Security code",
        "Card number",
        "IBAN",
        "Recovery phrase",
        "كلمة المرور",
        "رمز التحقق",
        "الرقم السري",
        "رقم البطاقة",
    ],
)
def test_credential_fields_are_refused_in_both_languages(label: str) -> None:
    """Every non-English label was invisible to the prototype's checks."""
    assert classify_typed_value(label, "anything").verdict is Verdict.REFUSE


def test_private_keys_and_seed_phrases_are_refused() -> None:
    assert (
        classify_typed_value("Notes", "-----BEGIN RSA PRIVATE KEY-----").verdict is Verdict.REFUSE
    )
    seed = " ".join(["abandon"] * 12)
    assert classify_typed_value("Notes", seed).verdict is Verdict.REFUSE


def test_ordinary_search_text_is_allowed() -> None:
    assert classify_typed_value("Search", "Libya news").verdict is Verdict.ALLOW
    assert classify_typed_value("بحث", "أخبار ليبيا").verdict is Verdict.ALLOW


# ---- the element being clicked --------------------------------------------


def test_the_resolved_label_is_what_gets_classified() -> None:
    """The prototype classified the query, so "Continue" hid "Continue to payment"."""
    assert classify_click("Continue").verdict is Verdict.ALLOW
    assert classify_click("Continue to payment").verdict is Verdict.NEEDS_APPROVAL


@pytest.mark.parametrize(
    "label",
    [
        "Buy now",
        "Place order",
        "Confirm order",
        "Pay",
        "Subscribe",
        "Delete account",
        "Transfer funds",
        "Withdraw",
        "Publish",
        "Post",
        "Submit application",
        "Add to cart",
        "Accept cookies",
        "I agree",
        "شراء",
        "ادفع",
        "تأكيد الطلب",
        "حذف",
        "نشر",
        "أوافق",
        "إرسال",
    ],
)
def test_consequential_clicks_require_approval_in_both_languages(label: str) -> None:
    assert classify_click(label).verdict is Verdict.NEEDS_APPROVAL


@pytest.mark.parametrize("label", ["Mac", "Read more", "Next page", "المزيد", "الرئيسية"])
def test_ordinary_navigation_clicks_are_allowed(label: str) -> None:
    assert classify_click(label).verdict is Verdict.ALLOW


@pytest.mark.parametrize(
    "label", ["Verify you are human", "I'm not a robot", "reCAPTCHA", "لست روبوت"]
)
def test_captcha_is_always_refused_never_merely_gated(label: str) -> None:
    decision = classify_click(label)
    assert decision.verdict is Verdict.REFUSE
    assert "CAPTCHA" in decision.reason or "bot" in decision.reason


def test_sensitive_field_values_are_never_read_back() -> None:
    assert redact_field_value("Password", "hunter2") == "«hidden»"
    assert redact_field_value("Search", "libya") == "libya"


# ---- registry wiring ------------------------------------------------------


async def test_click_and_type_require_approval_before_running() -> None:
    """A consequential tool must not execute on the first call."""
    engine = ToolEngine(registry)

    for tool_id, args in (
        ("browser_click", {"target": "Buy now"}),
        ("browser_type", {"field": "Search", "value": "x"}),
    ):
        invocation = await engine.execute(tool_id, args)
        assert invocation.status.value == "waiting_for_approval", tool_id
        # Nothing ran: no browser was launched, no error either.
        assert invocation.error is None


async def test_navigation_tools_are_deterministic_and_need_no_model() -> None:
    for tool_id in ("browser_open", "browser_back", "browser_reload", "browser_links"):
        spec = registry.get(tool_id)
        assert spec is not None, tool_id
        assert spec.deterministic is True, tool_id
        assert spec.requires_model_followup is False, tool_id


async def test_browser_tools_fail_cleanly_without_a_page() -> None:
    """No page open must be a named failure, not a crash."""
    engine = ToolEngine(registry)
    invocation = await engine.execute("browser_reload", {})
    assert invocation.status.value == "failed"
    assert invocation.error is not None
    assert invocation.error["code"] == "no_page"


def test_inspect_fences_page_text_as_untrusted() -> None:
    spec = registry.get("browser_inspect")
    assert spec is not None
    # The page text needs a model to interpret it, so it is NOT deterministic.
    assert spec.deterministic is False
    assert spec.requires_model_followup is True
