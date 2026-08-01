"""Web tools: open_public_url, read_article, web_search, news_search.

All four are ``READ_PUBLIC`` and none is deterministic — a page's text is
evidence the model must reason over and cite, not an answer in itself. That is
the opposite of the calculator, and the registry enforces the distinction.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from kimi.research.extract import ArticleExtractor
from kimi.research.models import Source
from kimi.research.net import SafeFetcher, UnsafeUrlError, validate_url
from kimi.research.pipeline import ResearchPipeline
from kimi.tools.base import (
    PermissionLevel,
    Renderer,
    ToolContext,
    ToolFailure,
    ToolOutcome,
    ToolSpec,
)
from kimi.tools.registry import warn

# A single shared fetcher/pipeline so connections are pooled rather than a fresh
# client per request, which the audit found at six separate call sites.
_fetcher = SafeFetcher()
_pipeline = ResearchPipeline(fetcher=_fetcher)
_extractor = ArticleExtractor(fetcher=_fetcher)


def _set_pipeline(pipeline: ResearchPipeline) -> None:
    """Test hook."""
    global _pipeline
    _pipeline = pipeline


def _set_extractor(extractor: ArticleExtractor) -> None:
    """Test hook."""
    global _extractor
    _extractor = extractor


# ---------------------------------------------------------------------------
# open_public_url
# ---------------------------------------------------------------------------


class OpenUrlInput(BaseModel):
    url: Annotated[str, Field(max_length=2048, description="An http(s) URL to read.")]


class OpenUrlOutput(BaseModel):
    url: str
    title: str
    publisher: str
    status: str
    status_label: str
    retrieval: str
    published_at: str | None = None
    date_verified: bool = False
    content: str = ""
    aggregator_url: str | None = None


async def _open_url(payload: OpenUrlInput, _c: ToolContext) -> ToolOutcome[OpenUrlOutput]:
    try:
        validate_url(payload.url)
    except UnsafeUrlError as exc:
        # The message names the policy, never the resolved internal address.
        raise ToolFailure(
            "unsafe_url",
            "That address cannot be opened. Only public web pages are allowed.",
            detail=str(exc),
        ) from exc

    source = Source(url=payload.url, title="")
    source = await _extractor.extract(source)

    warnings = []
    if not source.status.has_body:
        warnings.append(
            warn(
                "not_readable",
                "The page was reached but its text could not be extracted "
                f"({source.status.label}).",
            )
        )

    return ToolOutcome(
        value=OpenUrlOutput(
            url=source.url,
            title=source.title,
            publisher=source.publisher,
            status=str(source.status),
            status_label=source.status.label,
            retrieval=str(source.retrieval),
            published_at=source.published_at.isoformat() if source.published_at else None,
            date_verified=source.date_verified,
            content=source.content,
            aggregator_url=source.aggregator_url,
        ),
        warnings=warnings,
    )


OPEN_PUBLIC_URL = ToolSpec(
    id="open_public_url",
    name="Open page",
    description=(
        "Fetch a public web page and return its readable text, title, publisher "
        "and publication date. Only public http(s) addresses are allowed."
    ),
    input_model=OpenUrlInput,
    output_model=OpenUrlOutput,
    handler=_open_url,
    deterministic=False,
    requires_model_followup=True,
    timeout_s=25.0,
    permission=PermissionLevel.READ_PUBLIC,
    renderer=Renderer.ARTICLE,
    audit_event="tool.open_public_url",
)


# ---------------------------------------------------------------------------
# read_article
# ---------------------------------------------------------------------------


class ReadArticleInput(BaseModel):
    url: Annotated[str, Field(max_length=2048)]
    resolve_publisher: bool = Field(
        default=True,
        description="Resolve aggregator links (Google News etc.) to the publisher.",
    )


async def _read_article(
    payload: ReadArticleInput, context: ToolContext
) -> ToolOutcome[OpenUrlOutput]:
    if not payload.resolve_publisher:
        return await _open_url(OpenUrlInput(url=payload.url), context)

    try:
        validate_url(payload.url)
    except UnsafeUrlError as exc:
        raise ToolFailure(
            "unsafe_url",
            "That address cannot be opened. Only public web pages are allowed.",
            detail=str(exc),
        ) from exc

    source = await _extractor.extract(Source(url=payload.url, title=""))
    warnings = []
    if source.aggregator_url:
        warnings.append(
            warn("resolved_publisher", "The aggregator link was resolved to the publisher.")
        )
    if not source.status.has_body:
        warnings.append(warn("not_readable", f"Article not readable: {source.status.label}."))

    return ToolOutcome(
        value=OpenUrlOutput(
            url=source.url,
            title=source.title,
            publisher=source.publisher,
            status=str(source.status),
            status_label=source.status.label,
            retrieval=str(source.retrieval),
            published_at=source.published_at.isoformat() if source.published_at else None,
            date_verified=source.date_verified,
            content=source.content,
            aggregator_url=source.aggregator_url,
        ),
        warnings=warnings,
    )


READ_ARTICLE = ToolSpec(
    id="read_article",
    name="Read article",
    description=(
        "Read a news article, resolving aggregator links to the original "
        "publisher and reporting how much of the article could be retrieved."
    ),
    input_model=ReadArticleInput,
    output_model=OpenUrlOutput,
    handler=_read_article,
    deterministic=False,
    requires_model_followup=True,
    timeout_s=30.0,
    permission=PermissionLevel.READ_PUBLIC,
    renderer=Renderer.ARTICLE,
    audit_event="tool.read_article",
)


# ---------------------------------------------------------------------------
# web_search / news_search
# ---------------------------------------------------------------------------


class SearchInput(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=500)]
    max_sources: Annotated[int, Field(ge=1, le=12)] = 6
    read_articles: bool = Field(
        default=True, description="Fetch article bodies rather than headlines only."
    )


class SearchOutput(BaseModel):
    topic: str
    freshness: dict[str, Any]
    queries: list[str]
    generated_at: str
    sources: list[dict[str, Any]]
    providers: dict[str, Any]
    counts: dict[str, Any]
    timing: dict[str, Any]
    warnings: list[str]
    prompt_block: str
    """The fenced untrusted-content block for the model."""


async def _run_search(payload: SearchInput, *, extract: bool) -> ToolOutcome[SearchOutput]:
    report = await _pipeline.run(payload.query, max_sources=payload.max_sources, extract=extract)

    # Validate the whole payload in one step rather than casting field by field.
    value = SearchOutput.model_validate(
        {**report.to_payload(), "prompt_block": report.to_prompt_block()}
    )

    warnings = [warn("research", w) for w in report.warnings]
    if not report.has_sources and not report.warnings:
        warnings.append(warn("no_results", "No sources matched that request."))

    return ToolOutcome(value=value, warnings=warnings)


async def _web_search(payload: SearchInput, _c: ToolContext) -> ToolOutcome[SearchOutput]:
    return await _run_search(payload, extract=payload.read_articles)


async def _news_search(payload: SearchInput, _c: ToolContext) -> ToolOutcome[SearchOutput]:
    return await _run_search(payload, extract=payload.read_articles)


WEB_SEARCH = ToolSpec(
    id="web_search",
    name="Web search",
    description=(
        "Search the public web and return sources with titles, publishers, "
        "publication dates and an explicit note of how much of each page was read."
    ),
    input_model=SearchInput,
    output_model=SearchOutput,
    handler=_web_search,
    deterministic=False,
    requires_model_followup=True,
    timeout_s=60.0,
    permission=PermissionLevel.READ_PUBLIC,
    renderer=Renderer.SOURCES,
    audit_event="tool.web_search",
)

NEWS_SEARCH = ToolSpec(
    id="news_search",
    name="News search",
    description=(
        "Search current news across several providers with an exact freshness "
        "window. When the request names a window such as 'the last 24 hours', "
        "only articles with a verified publication date inside it are returned."
    ),
    input_model=SearchInput,
    output_model=SearchOutput,
    handler=_news_search,
    deterministic=False,
    requires_model_followup=True,
    timeout_s=75.0,
    permission=PermissionLevel.READ_PUBLIC,
    renderer=Renderer.SOURCES,
    audit_event="tool.news_search",
)


async def aclose() -> None:
    await _pipeline.aclose()
