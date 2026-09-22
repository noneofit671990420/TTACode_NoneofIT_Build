"""Web tools: dependency-free search and page fetch.

* ``web_search`` — DuckDuckGo HTML endpoints via ``urllib`` (no API key).
  Parses the top results (title / url / snippet) with regexes. Tries the
  main HTML endpoint, then the lite endpoint as fallback.
* ``web_fetch`` — GET a page and return plain text (scripts/styles
  stripped naively), truncated.

Both are fully defensive: network blocks, captchas, layout changes and
timeouts all become ``{"ok": False, "error": ...}`` results for the
model — never exceptions, never hangs.
"""

from __future__ import annotations

import html as _html
import re
import urllib.parse
import urllib.request

from ..context import ToolContext
from ..schema import function_schema

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)
_SEARCH_TIMEOUT = 20
_FETCH_TIMEOUT = 25
_FETCH_MAX_CHARS = 12_000

# DuckDuckGo HTML result link: <a rel="nofollow" class="result__a" href="URL">Title</a>
_RESULT_LINK = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I
)
# Snippet next to it: <a class="result__snippet" ...> ... </a>  (or td/div variants)
_RESULT_SNIPPET = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>', re.S | re.I
)
_TAG = re.compile(r"<[^>]+>")


def _strip_tags(fragment: str) -> str:
    text = _TAG.sub(" ", fragment)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _ddg_search(query: str, endpoint: str, max_results: int) -> list[dict]:
    url = endpoint + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_SEARCH_TIMEOUT) as response:
        page = response.read().decode("utf-8", errors="replace")
    links = _RESULT_LINK.findall(page)
    snippets = _RESULT_SNIPPET.findall(page)
    results: list[dict] = []
    for i, (href, title_html) in enumerate(links):
        if len(results) >= max_results:
            break
        href = _html.unescape(href).strip()
        # DuckDuckGo wraps outbound links in a redirect; unwrap it.
        if href.startswith("//duckduckgo.com/l/?uddg="):
            href = urllib.parse.unquote(href.split("uddg=", 1)[1].split("&")[0])
        elif href.startswith("/l/?uddg="):
            href = urllib.parse.unquote(href.split("uddg=", 1)[1].split("&")[0])
        title = _strip_tags(title_html)
        if not href.startswith(("http://", "https://")) or not title:
            continue
        snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
        results.append({"title": title, "url": href, "snippet": snippet[:500]})
    return results


def register_tools(registry, ctx: ToolContext) -> None:
    def web_search(args: dict) -> dict:
        query = str(args.get("query", "")).strip()
        if not query:
            return {"ok": False, "error": "query must not be empty."}
        try:
            max_results = int(args.get("max_results", 8))
        except (TypeError, ValueError):
            max_results = 8
        max_results = max(1, min(max_results, 20))
        errors: list[str] = []
        for endpoint in ("https://html.duckduckgo.com/html/?",
                         "https://lite.duckduckgo.com/lite/?"):
            try:
                results = _ddg_search(query, endpoint, max_results)
            except Exception as exc:  # network, captcha, layout change...
                errors.append(f"{endpoint}: {exc}")
                continue
            if results:
                return {"ok": True, "query": query, "results": results}
            errors.append(f"{endpoint}: no results parsed")
        return {"ok": False, "error": "Web search failed: " + " | ".join(errors)}

    def web_fetch(args: dict) -> dict:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "url must start with http:// or https://"}
        try:
            max_chars = int(args.get("max_chars", _FETCH_MAX_CHARS))
        except (TypeError, ValueError):
            max_chars = _FETCH_MAX_CHARS
        max_chars = max(500, min(max_chars, 60_000))
        try:
            request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as response:
                raw = response.read(2_000_000).decode("utf-8", errors="replace")
        except Exception as exc:
            return {"ok": False, "error": f"Fetch failed: {exc}"}
        # Drop scripts/styles first, then all remaining tags.
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ",
                      raw, flags=re.S | re.I)
        text = _strip_tags(text)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…[truncated]"
        return {"ok": True, "url": url, "text": text}

    registry.register(
        "web_search",
        "Search the web (DuckDuckGo, no API key). Returns top results with "
        "title, url and snippet. Use to look up docs, errors, libraries.",
        function_schema(
            "web_search",
            "Search the web and return top results (title/url/snippet).",
            {
                "query": "Search query",
                "max_results": {"type": "integer",
                                "description": "Max results, 1-20",
                                "required": False, "default": 8},
            },
        ),
        web_search,
    )
    registry.register(
        "web_fetch",
        "Fetch a web page and return its plain text (scripts/styles "
        "removed), truncated. Use after web_search to read a result.",
        function_schema(
            "web_fetch",
            "Fetch a URL and return plain-text content.",
            {
                "url": "http(s):// URL to fetch",
                "max_chars": {"type": "integer",
                              "description": "Max text chars, 500-60000",
                              "required": False, "default": _FETCH_MAX_CHARS},
            },
        ),
        web_fetch,
    )
