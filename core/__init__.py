"""Core tools for the Kimi Workspace app.

The news search in ``web_tools`` is patched at package import time with a
multi-provider implementation. This keeps the rest of the application API
stable while adding Google News RSS and GDELT fallbacks when DDGS providers
return no results.
"""

from . import web_tools as _web_tools
from .news_fallback import search_news_robust

_web_tools.search_news = search_news_robust
