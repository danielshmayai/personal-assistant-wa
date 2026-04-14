"""Web tools — give the agent live internet access.

Tools:
  web_search      — Tavily (if TAVILY_API_KEY set) or DuckDuckGo fallback
  wikipedia_search — factual / encyclopedic queries
  fetch_url        — read any web page the user shares
  get_weather      — current weather via wttr.in (no key needed)
"""

import logging
import httpx
from langchain_core.tools import tool
from app.config import TAVILY_API_KEY

logger = logging.getLogger("pa.web")

# Max characters returned to the LLM — keeps context window sane
_MAX_CHARS = 3000


@tool
def web_search(query: str) -> str:
    """Search the internet for current information: news, prices, events, facts, people, etc.
    Use this whenever the user asks about anything that might have changed recently
    or that requires up-to-date data. Prefer this over guessing."""
    if TAVILY_API_KEY:
        return _tavily_search(query)
    return _ddg_search(query)


@tool
def wikipedia_search(query: str) -> str:
    """Look up encyclopedic or factual information on Wikipedia.
    Best for: historical facts, scientific concepts, biographies, definitions.
    Use web_search instead for current events or recent data."""
    try:
        import wikipediaapi
        wiki = wikipediaapi.Wikipedia(
            language="en",
            user_agent="danidin-bot/1.0 (personal-assistant)",
        )
        page = wiki.page(query)
        if not page.exists():
            # Try a web_search-style fallback by searching Wikipedia's API
            return _wikipedia_search_fallback(query)
        # Return the first ~1500 chars of the summary + article URL
        summary = page.summary[:1500]
        if len(page.summary) > 1500:
            summary += "…"
        return f"{summary}\n\nSource: {page.fullurl}"
    except Exception as e:
        logger.exception("Wikipedia search failed")
        return f"Wikipedia lookup failed: {e}"


def _wikipedia_search_fallback(query: str) -> str:
    """Use Wikipedia's opensearch API to find the closest article title, then fetch it."""
    try:
        import urllib.parse
        import json
        resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": 3,
                "format": "json",
            },
            timeout=10.0,
            headers={"User-Agent": "danidin-bot/1.0"},
        )
        data = resp.json()
        titles = data[1] if len(data) > 1 else []
        if not titles:
            return f"No Wikipedia article found for '{query}'."
        import wikipediaapi
        wiki = wikipediaapi.Wikipedia(
            language="en",
            user_agent="danidin-bot/1.0 (personal-assistant)",
        )
        page = wiki.page(titles[0])
        if not page.exists():
            return f"No Wikipedia article found for '{query}'."
        summary = page.summary[:1500]
        return f"{summary}\n\nSource: {page.fullurl}"
    except Exception as e:
        return f"Wikipedia lookup failed: {e}"


@tool
async def fetch_url(url: str) -> str:
    """Fetch and read the visible text content of any web page URL.
    Use this when the user pastes a link and asks you to read, summarise,
    or answer questions about it."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; danidin-bot/1.0)"})
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        return f"HTTP {e.response.status_code} fetching {url}"
    except Exception as e:
        return f"Failed to fetch {url}: {e}"

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    except Exception:
        # Fallback: raw text stripped of HTML tags
        import re
        text = re.sub(r"<[^>]+>", " ", r.text)

    text = "\n".join(line for line in text.splitlines() if line.strip())
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "\n… [page truncated]"
    return text or "(page appears to be empty)"


@tool
async def get_weather(location: str) -> str:
    """Get the current weather for any city or location.
    Returns temperature, conditions, wind, and humidity.
    Example: get_weather("Tel Aviv") or get_weather("London, UK")"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"https://wttr.in/{location}",
                params={"format": "4", "lang": "en"},
                headers={"User-Agent": "curl/7.0"},
            )
            return r.text.strip() or f"No weather data for '{location}'."
    except Exception as e:
        logger.exception("Weather lookup failed for %s", location)
        return f"Weather lookup failed: {e}"


# ── Private helpers ──────────────────────────────────────────────────────────

def _tavily_search(query: str) -> str:
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        resp = client.search(query, max_results=3, include_answer=True)

        parts = []
        if resp.get("answer"):
            parts.append(f"*Summary:* {resp['answer']}")

        for r in resp.get("results", []):
            snippet = (r.get("content") or "")[:400]
            parts.append(f"• *{r['title']}*\n  {r['url']}\n  {snippet}")

        return "\n\n".join(parts) or "No results."
    except Exception as e:
        logger.exception("Tavily search failed, falling back to DDG")
        return _ddg_search(query)


def _ddg_search(query: str) -> str:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return "No results found."
        parts = [
            f"• *{r['title']}*\n  {r['href']}\n  {r['body'][:300]}"
            for r in results
        ]
        return "\n\n".join(parts)
    except Exception as e:
        logger.exception("DuckDuckGo search failed")
        return f"Web search unavailable: {e}"


# Exported list for graph nodes
WEB_TOOLS = [web_search, wikipedia_search, fetch_url, get_weather]
