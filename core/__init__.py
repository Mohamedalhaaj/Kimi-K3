"""Core tools for the Kimi Workspace app.

The public API in ``web_tools`` is patched at package import time with:

- an isolated local Playwright browser for rendered public pages and explicit
  ``/browser`` commands;
- a multi-provider recent-news pipeline;
- publisher-link resolution and full-article extraction where possible.
"""

from . import web_tools as _web_tools
from .browser_agent import install_browser_patches

# Install the browser reader before importing article/news modules so their
# local ``fetch_public_page`` references inherit the Playwright fallback.
install_browser_patches(_web_tools)

from .article_resolver import enrich_news_sources
from .news_resilient import search_news_resilient as _provider_news_search


def search_news_resilient(
    query: str,
    max_results: int = 6,
    timelimit: str = "w",
):
    sources = _provider_news_search(
        query=query,
        max_results=max_results,
        timelimit=timelimit,
    )
    enrich_news_sources(sources, limit=min(max_results, 5))

    for source in sources:
        status = "Full article read" if source.content else "Headline/snippet only"
        existing = (source.snippet or "").strip()
        source.snippet = f"{status} — {existing}" if existing else status

    return sources


_web_tools.search_news = search_news_resilient
