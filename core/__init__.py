"""Core tools for the Kimi Workspace app.

The public API in ``web_tools`` is patched at package import time with a
multi-provider recent-news pipeline. Conversational prompts are reduced to
their actual topic, Google/Bing/DDGS/GDELT are tried with strict freshness
filtering, and aggregator links are resolved to publisher article pages before
the results are passed to the model.
"""

from . import web_tools as _web_tools
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
