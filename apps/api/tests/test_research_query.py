"""Query understanding — the two pure-function bugs the audit found.

AUDIT §5: news_resilient.py:105 reduced "U.S. tariffs" to the topic "U".
AUDIT §5: news_fallback.py:104,116 enforced 32 hours for "last 24 hours".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kimi.research.query import (
    UNBOUNDED,
    Intent,
    classify_intent,
    extract_topic,
    extract_urls,
    parse_freshness,
    plan_research,
)

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)


# ---- abbreviation preservation -------------------------------------------


@pytest.mark.parametrize(
    ("text", "must_contain"),
    [
        ("Give me the latest news about U.S. tariffs", "U.S."),
        ("What is the latest news on U.N. sanctions", "U.N."),
        ("news about the U.A.E. economy", "U.A.E."),
        ("latest on No. 10 Downing Street", "No. 10"),
        ("news about a 3.5% rate rise", "3.5%"),
        ("what's happening with Ph.D. funding", "Ph.D."),
        ("news about St. Petersburg", "St. Petersburg"),
    ],
)
def test_abbreviations_survive_topic_extraction(text: str, must_contain: str) -> None:
    topic = extract_topic(text)
    assert must_contain in topic, f"{must_contain!r} was destroyed: {topic!r}"
    # The specific regression: a single-letter topic.
    assert len(topic) > 2


def test_the_exact_audited_query() -> None:
    """The verbatim example from the audit."""
    topic = extract_topic("Give me the latest news about U.S. tariffs")
    assert topic != "U"
    assert "tariffs" in topic.lower()
    assert "U.S." in topic


def test_lead_ins_are_stripped_but_subject_kept() -> None:
    assert extract_topic("Give me the latest news about Libya") == "Libya"
    assert extract_topic("What are the latest news updates on Libya") == "Libya"
    assert extract_topic("search for Libya oil production") == "Libya oil production"
    assert "Libya" in extract_topic("tell me about Libya")


def test_freshness_phrases_are_removed_from_the_topic() -> None:
    topic = extract_topic("Libya news in the last 24 hours")
    assert "24" not in topic
    assert "Libya" in topic


def test_topic_never_becomes_empty() -> None:
    # A query that is nothing but a lead-in still yields something searchable.
    assert extract_topic("give me the latest news").strip() != ""
    assert extract_topic("news").strip() != ""


# ---- Arabic ---------------------------------------------------------------


def test_arabic_topic_extraction() -> None:
    topic = extract_topic("أعطني آخر الأخبار عن ليبيا")
    assert "ليبيا" in topic
    assert "أعطني" not in topic


def test_arabic_punctuation_is_not_treated_as_a_separator() -> None:
    topic = extract_topic("ما هي أخبار ليبيا والجزائر؟")
    assert "ليبيا" in topic
    assert "الجزائر" in topic


def test_arabic_freshness_is_parsed() -> None:
    window = parse_freshness("آخر أخبار ليبيا خلال 24 ساعة")
    assert window.hours == 24
    assert window.explicit is True


def test_arabic_digits_are_normalised() -> None:
    window = parse_freshness("أخبار ليبيا آخر ٢٤ ساعة")
    assert window.hours == 24
    assert window.explicit is True


# ---- freshness ------------------------------------------------------------


def test_last_24_hours_is_exactly_24_hours() -> None:
    """AUDIT §5: the prototype enforced 30h + a 2h future bound = 32 hours."""
    window = parse_freshness("Libya news from the last 24 hours")
    assert window.hours == 24
    assert window.explicit is True
    assert window.requires_dates is True

    cutoff = window.cutoff(NOW)
    assert cutoff == NOW - timedelta(hours=24)


@pytest.mark.parametrize(
    ("offset_hours", "expected"),
    [
        (0.0, True),
        (1.0, True),
        (23.5, True),
        (23.99, True),
        (24.5, False),
        (25.0, False),
        (30.0, False),  # the prototype accepted this
        (32.0, False),  # and this
        (48.0, False),
    ],
)
def test_24_hour_boundary(offset_hours: float, expected: bool) -> None:
    window = parse_freshness("last 24 hours")
    published = NOW - timedelta(hours=offset_hours)
    assert window.contains(published, NOW) is expected


def test_exact_boundary_instant_is_inside() -> None:
    window = parse_freshness("last 24 hours")
    assert window.contains(NOW - timedelta(hours=24), NOW) is True
    assert window.contains(NOW - timedelta(hours=24, seconds=1), NOW) is False


def test_future_dates_are_rejected_beyond_clock_skew() -> None:
    window = parse_freshness("last 24 hours")
    assert window.contains(NOW + timedelta(minutes=2), NOW) is True  # skew
    assert window.contains(NOW + timedelta(hours=3), NOW) is False


def test_undated_articles_are_excluded_when_a_window_is_explicit() -> None:
    explicit = parse_freshness("last 24 hours")
    assert explicit.requires_dates is True
    assert explicit.contains(None, NOW) is False

    # With no explicit window, undated results are allowed through.
    assert UNBOUNDED.contains(None, NOW) is True


def test_latest_is_soft_and_never_silently_explicit() -> None:
    """The prototype mapped "latest" to an 8-day window and said nothing."""
    window = parse_freshness("give me the latest news on Libya")
    assert window.explicit is False
    assert window.requires_dates is False
    assert window.hours == 168
    # The label states what was actually used.
    assert "default" in window.label


@pytest.mark.parametrize(
    ("text", "hours"),
    [
        ("last 6 hours", 6),
        ("past 12 hours", 12),
        ("within the last 48 hours", 48),
        ("last 3 days", 72),
        ("today", 24),
        ("this week", 168),
        ("last month", 720),
    ],
)
def test_named_and_numeric_windows(text: str, hours: int) -> None:
    window = parse_freshness(text)
    assert window.hours == hours
    assert window.explicit is True


def test_no_freshness_signal_is_unbounded() -> None:
    window = parse_freshness("who designed the Eiffel Tower")
    assert window.hours is None
    assert window.requires_dates is False


def test_naive_datetime_is_rejected_loudly() -> None:
    window = parse_freshness("last 24 hours")
    with pytest.raises(ValueError, match="timezone-aware"):
        window.contains(datetime(2026, 7, 31, 11, 0, 0), NOW)


# ---- urls and intent ------------------------------------------------------


def test_urls_are_extracted_and_trailing_punctuation_trimmed() -> None:
    urls = extract_urls("Read https://example.com/a-story, then tell me.")
    assert urls == ["https://example.com/a-story"]


def test_bare_filenames_are_never_treated_as_urls() -> None:
    """AUDIT §5: BARE_DOMAIN_PATTERN made "app.py" fetch https://app.py."""
    for text in ("what does app.py do?", "explain next.js", "open index.html", "main.rs"):
        assert extract_urls(text) == []


def test_intent_classification() -> None:
    assert classify_intent("https://example.com/story") is Intent.READ_URL
    assert classify_intent("latest news on Libya") is Intent.NEWS
    assert classify_intent("Libya in the last 24 hours") is Intent.NEWS
    assert classify_intent("who designed the Eiffel Tower") is Intent.WEB


def test_plan_builds_multiple_queries_for_news() -> None:
    plan = plan_research("Libya news from the last 24 hours")
    assert plan.intent is Intent.NEWS
    assert plan.freshness.hours == 24
    assert plan.freshness.explicit is True
    assert len(plan.queries) >= 2
    assert all(q.strip() for q in plan.queries)
    assert any("Libya" in q for q in plan.queries)


def test_plan_detects_arabic_and_queries_in_arabic() -> None:
    plan = plan_research("أخبار ليبيا خلال 24 ساعة")
    assert plan.is_arabic is True
    assert plan.freshness.hours == 24
    assert any("ليبيا" in q for q in plan.queries)
    # It must not append an English "news" token to an Arabic query.
    assert not any("news" in q.lower() for q in plan.queries)


def test_site_restriction_is_extracted_not_searched() -> None:
    plan = plan_research("site:reuters.com Libya oil")
    assert plan.domains == ["reuters.com"]
    assert "site:" not in " ".join(plan.queries)
    assert "Libya oil" in plan.queries[0]
