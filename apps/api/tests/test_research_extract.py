"""Extraction, canonical resolution and status labelling."""

from __future__ import annotations

import pytest

from kimi.research.extract import (
    canonicalise,
    classify_body,
    extract_canonical,
    extract_title,
    html_to_text,
    is_aggregator,
    looks_like_article_url,
    resolve_redirect_param,
)
from kimi.research.models import ExtractionStatus

# ---- homepages must never be presented as articles ------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://reuters.com",
        "https://reuters.com/",
        "https://www.bbc.com/",
        "https://example.com/news",
        "https://example.com/world",
        "https://example.com/index.html",
        "https://example.com/latest/",
    ],
)
def test_homepages_and_section_fronts_are_not_articles(url: str) -> None:
    assert looks_like_article_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://reuters.com/world/africa/libya-oil-output-2026-07-31/",
        "https://bbc.com/news/articles/c123abc",
        "https://example.com/2026/07/31/some-story-slug",
        "https://example.com/a-long-hyphenated-headline",
    ],
)
def test_real_article_urls_are_accepted(url: str) -> None:
    assert looks_like_article_url(url) is True


# ---- canonicalisation and dedup keys --------------------------------------


def test_tracking_parameters_are_stripped() -> None:
    a = canonicalise("https://www.example.com/story?utm_source=x&utm_medium=y&id=7")
    b = canonicalise("https://example.com/story/?id=7&fbclid=abc")
    assert a == b
    assert "utm_" not in a and "fbclid" not in a


def test_fragment_and_trailing_slash_are_normalised() -> None:
    assert canonicalise("https://example.com/a/#section") == canonicalise("https://example.com/a")


# ---- aggregator resolution ------------------------------------------------


def test_aggregator_hosts_are_recognised() -> None:
    assert is_aggregator("https://news.google.com/articles/abc") is True
    assert is_aggregator("https://www.bing.com/news/x") is True
    assert is_aggregator("https://reuters.com/world/x-2026") is False


def test_redirect_parameter_is_extracted() -> None:
    url = "https://news.google.com/redirect?url=https%3A%2F%2Freuters.com%2Fworld%2Fstory-2026"
    from urllib.parse import unquote

    assert unquote(resolve_redirect_param(url) or "") == "https://reuters.com/world/story-2026"


def test_redirect_param_pointing_at_the_same_host_is_ignored() -> None:
    url = "https://news.google.com/x?url=https://news.google.com/other"
    assert resolve_redirect_param(url) is None


def test_canonical_link_is_read_from_real_signals_only() -> None:
    """AUDIT §5: the prototype fell back to the first <a href> on the page."""
    html = """
    <html><head>
      <link rel="canonical" href="https://reuters.com/world/africa/real-story-2026">
    </head><body>
      <a href="https://example.com/nav-link-first">Nav</a>
    </body></html>
    """
    assert (
        extract_canonical(html, "https://news.google.com/x")
        == "https://reuters.com/world/africa/real-story-2026"
    )


def test_no_canonical_signal_yields_none_rather_than_a_nav_link() -> None:
    html = """
    <html><body>
      <a href="https://example.com/some-promo-link">Promo</a>
      <a href="https://example.com/another-link">Other</a>
    </body></html>
    """
    # The prototype returned the first anchor here. We return nothing.
    assert extract_canonical(html, "https://news.google.com/x") is None


def test_og_url_is_accepted_as_canonical() -> None:
    html = '<meta property="og:url" content="https://bbc.com/news/articles/c1">'
    assert extract_canonical(html, "https://news.google.com/x") == (
        "https://bbc.com/news/articles/c1"
    )


def test_canonical_pointing_back_at_an_aggregator_is_refused() -> None:
    html = '<link rel="canonical" href="https://news.google.com/self">'
    assert extract_canonical(html, "https://news.google.com/x") is None


# ---- body classification --------------------------------------------------


def test_full_article_is_labelled_full() -> None:
    assert classify_body("word " * 400) is ExtractionStatus.FULL


def test_thin_body_is_partial_not_full() -> None:
    assert classify_body("word " * 60) is ExtractionStatus.PARTIAL


def test_stub_is_snippet_only() -> None:
    assert classify_body("Short.") is ExtractionStatus.SNIPPET_ONLY


@pytest.mark.parametrize("status", [401, 403, 451])
def test_http_refusals_are_blocked(status: int) -> None:
    assert classify_body("word " * 400, http_status=status) is ExtractionStatus.BLOCKED


def test_bot_wall_text_is_blocked_even_with_a_200() -> None:
    assert classify_body("Please verify you are human " * 60) is ExtractionStatus.BLOCKED


def test_paywall_text_is_labelled_paywalled() -> None:
    body = "Subscribe to continue reading this article. " * 40
    assert classify_body(body) is ExtractionStatus.PAYWALLED


def test_arabic_paywall_marker() -> None:
    body = "للمشتركين فقط " * 80
    assert classify_body(body) is ExtractionStatus.PAYWALLED


def test_status_labels_are_human_readable() -> None:
    assert ExtractionStatus.FULL.label == "Full article read"
    assert ExtractionStatus.PARTIAL.label == "Partial article read"
    assert ExtractionStatus.SNIPPET_ONLY.label == "Headline/snippet only"
    assert ExtractionStatus.PAYWALLED.label == "Paywalled"
    assert ExtractionStatus.BLOCKED.label == "Blocked"
    assert ExtractionStatus.FULL.has_body and not ExtractionStatus.BLOCKED.has_body


# ---- html to text ---------------------------------------------------------


def test_scripts_and_styles_are_removed() -> None:
    html = "<p>Real text</p><script>var x = 'hidden';</script><style>.a{}</style>"
    text = html_to_text(html)
    assert "Real text" in text
    assert "hidden" not in text and ".a{}" not in text


def test_entities_are_decoded_and_blocks_separated() -> None:
    text = html_to_text("<p>A &amp; B</p><p>Second</p>")
    assert "A & B" in text
    assert "Second" in text


def test_title_extraction_prefers_og_title() -> None:
    html = '<meta property="og:title" content="The Real Headline"><title>Site — Section</title>'
    assert extract_title(html) == "The Real Headline"


def test_arabic_text_survives_extraction() -> None:
    text = html_to_text("<p>طرابلس، ليبيا — تقرير جديد</p>")
    assert "طرابلس" in text and "ليبيا" in text
