"""What the browser agent is allowed to do.

The prototype's guards were 13- and 12-entry **English substring** denylists
checked against the *user's search string* — never against the element that was
actually resolved, and never against the value being typed (AUDIT §5,
browser_agent.py:14-28, 95-101, 194, 216-227). So ``/browser click Continue``
could resolve to "Continue to payment", ``/browser type Email :: 482913`` typed
an OTP, and every non-English label was invisible to the checks — in an app that
advertises Arabic support.

This module fixes the shape of the problem, not just the word list:

* the **resolved element's own text** is what gets classified, not the query;
* the **value being typed** is classified separately, by pattern rather than by
  vocabulary, so a card number or a one-time code is caught in any language;
* consequential actions are **refused pending explicit approval** rather than
  blocked outright, so the user stays in control;
* Arabic terms are first-class, not an afterthought.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Verdict(StrEnum):
    ALLOW = "allow"
    NEEDS_APPROVAL = "needs_approval"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    verdict: Verdict
    reason: str = ""
    matched: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


# --------------------------------------------------------------------------
# Values that must never be typed, in any language
# --------------------------------------------------------------------------

#: 13-19 digits, optionally spaced or dashed — a payment card.
_CARD = re.compile(r"^(?:\d[ -]?){12,18}\d$")
#: A bare 4-8 digit code: OTP, PIN, CVV.
_SHORT_CODE = re.compile(r"^\d{4,8}$")
#: Crypto seed phrases and private keys.
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|^[a-f0-9]{64}$", re.IGNORECASE)
_SEED_PHRASE = re.compile(r"^(?:\b[a-z]{3,8}\b[ ]){11,23}\b[a-z]{3,8}\b$", re.IGNORECASE)

#: Field labels that indicate a credential input, in English and Arabic.
_SECRET_FIELD: Final = re.compile(
    r"\b(?:password|passwd|passcode|pin|otp|cvv|cvc|security\s*code|"
    r"one[\s-]*time|verification\s*code|card\s*number|iban|ssn|secret|"
    r"private\s*key|seed\s*phrase|recovery\s*(?:code|phrase))\b"
    r"|(?:كلمة\s*(?:المرور|السر)|رمز\s*(?:التحقق|الأمان|سري)|الرقم\s*السري|"
    r"رقم\s*البطاقة|المفتاح\s*الخاص)",
    re.IGNORECASE,
)


def classify_typed_value(field_label: str, value: str) -> SafetyDecision:
    """Refuse to type credentials, payment data, or one-time codes.

    Classified by *pattern* as well as by field label, because the label is
    attacker- and locale-controlled but the shape of a card number is not.
    """
    text = (value or "").strip()

    if _SECRET_FIELD.search(field_label or ""):
        return SafetyDecision(
            Verdict.REFUSE,
            "That field asks for a credential or security code. Type it yourself "
            "in the browser window — this agent never enters secrets.",
            matched=field_label[:60],
        )
    if _PRIVATE_KEY.search(text) or _SEED_PHRASE.match(text):
        return SafetyDecision(Verdict.REFUSE, "That value looks like a private key or seed phrase.")
    digits = re.sub(r"[ -]", "", text)
    if _CARD.match(text) and _luhn(digits):
        return SafetyDecision(Verdict.REFUSE, "That value looks like a payment card number.")
    if _SHORT_CODE.match(text):
        return SafetyDecision(
            Verdict.REFUSE,
            "That looks like a PIN or one-time code. Enter it yourself in the browser window.",
        )
    return SafetyDecision(Verdict.ALLOW)


def _luhn(digits: str) -> bool:
    """A real card number checksums; an order number usually does not."""
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


# --------------------------------------------------------------------------
# Actions that change something outside the app
# --------------------------------------------------------------------------

#: Consequential intents. Deliberately bilingual, and matched against the
#: RESOLVED element text rather than the user's query.
_CONSEQUENTIAL: Final = re.compile(
    r"\b(?:buy|purchase|checkout|check\s*out|place\s+order|confirm\s+order|"
    r"pay(?:ment)?|subscribe|donate|transfer|withdraw|send\s+money|"
    r"delete|remove|deactivate|close\s+account|cancel\s+subscription|"
    r"publish|post|submit|send|apply|accept|agree|consent|authorise|authorize|"
    r"add\s+to\s+(?:cart|basket)|book\s+now|order\s+now|sign\s+(?:up|in)|log\s*in)\b"
    r"|(?:شراء|اشتر|ادفع|الدفع|تأكيد\s*الطلب|اشتراك|تبرع|تحويل|سحب|"
    r"حذف|إزالة|إلغاء|نشر|إرسال|أرسل|موافق|أوافق|قبول|تسجيل\s*(?:الدخول|حساب))",
    re.IGNORECASE,
)

#: Never automated at all, regardless of approval.
_ALWAYS_REFUSED: Final = re.compile(
    r"\b(?:captcha|recaptcha|hcaptcha|i'?m\s+not\s+a\s+robot|verify\s+you\s+are\s+human)\b"
    r"|(?:لست\s*روبوت|التحقق\s*البشري)",
    re.IGNORECASE,
)


def classify_click(element_text: str, *, url: str = "") -> SafetyDecision:
    """Decide whether clicking the RESOLVED element needs approval.

    ``element_text`` must be the label of the element the locator actually
    matched. The prototype classified the user's query instead, which is why
    "click Continue" could silently activate "Continue to payment".
    """
    label = (element_text or "").strip()

    if _ALWAYS_REFUSED.search(label):
        return SafetyDecision(
            Verdict.REFUSE,
            "This agent does not solve CAPTCHAs or bot checks. Complete it "
            "yourself in the browser window.",
            matched=label[:80],
        )

    match = _CONSEQUENTIAL.search(label)
    if match:
        return SafetyDecision(
            Verdict.NEEDS_APPROVAL,
            f"“{label[:80]}” looks like it does something consequential "
            f"({match.group(0)}). Approve it to continue.",
            matched=match.group(0),
        )

    _ = url
    return SafetyDecision(Verdict.ALLOW)


#: Field names whose current value must never be read back into the transcript.
_SENSITIVE_READBACK: Final = _SECRET_FIELD


def redact_field_value(label: str, value: str) -> str:
    """Never surface the contents of a credential field."""
    if _SENSITIVE_READBACK.search(label or ""):
        return "«hidden»"
    return value
