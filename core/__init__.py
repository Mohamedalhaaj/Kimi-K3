"""Core tools for the Kimi Workspace app.

The news search in ``web_tools`` is patched at package import time with a
multi-provider implementation. The provider chain simplifies conversational
prompts into their real topic, then tries Google News RSS, Bing News RSS,
DDGS News and GDELT with strict freshness filtering.
"""

from . import web_tools as _web_tools
from .news_resilient import search_news_resilient

_web_tools.search_news = search_news_resilient
