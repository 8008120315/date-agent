from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import httpx

from ..config import get_settings


@dataclass
class WebSearchItem:
    title: str
    url: str
    snippet: str
    published_at: str | None
    source: str


@dataclass
class WebSearchResult:
    query: str
    provider: str
    items: list[WebSearchItem]
    notice: str | None = None


class SearchService:
    def __init__(self) -> None:
        settings = get_settings()
        self.tavily_api_key = settings.tavily_api_key
        self.timeout_sec = settings.web_search_timeout_sec
        self.max_results = settings.web_search_max_results

    def search(self, query: str, max_results: int | None = None) -> WebSearchResult:
        q = query.strip()
        if not q:
            raise RuntimeError("Search query is empty.")

        limit = max(1, min(max_results or self.max_results, 10))

        if self.tavily_api_key:
            try:
                return self._search_tavily(q, limit)
            except RuntimeError:
                # Fall back to RSS when Tavily fails.
                rss_result = self._search_google_news_rss(q, limit)
                rss_result.notice = "Tavily failed; fallback to Google News RSS."
                return rss_result

        return self._search_google_news_rss(q, limit)

    def _search_tavily(self, query: str, max_results: int) -> WebSearchResult:
        endpoint = "https://api.tavily.com/search"
        payload = {
            "api_key": self.tavily_api_key,
            "query": query,
            "search_depth": "advanced",
            "topic": "general",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                resp = client.post(endpoint, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Tavily request failed: {exc}") from exc

        data = resp.json()
        raw_items = data.get("results") or []
        items: list[WebSearchItem] = []
        for row in raw_items[:max_results]:
            url = (row.get("url") or "").strip()
            title = (row.get("title") or "").strip()
            snippet = (row.get("content") or "").strip()
            if not url or not title:
                continue
            published = self._normalize_date(row.get("published_date"))
            items.append(
                WebSearchItem(
                    title=title,
                    url=url,
                    snippet=snippet,
                    published_at=published,
                    source="tavily",
                )
            )

        return WebSearchResult(
            query=query,
            provider="tavily",
            items=items,
        )

    def _search_google_news_rss(self, query: str, max_results: int) -> WebSearchResult:
        q = quote_plus(query)
        url = (
            "https://news.google.com/rss/search"
            f"?q={q}&hl=en-US&gl=US&ceid=US:en"
        )
        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                resp = client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Google News RSS request failed: {exc}") from exc

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise RuntimeError("Google News RSS parse failed.") from exc

        items: list[WebSearchItem] = []
        for item in root.findall("./channel/item")[:max_results]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date_raw = (item.findtext("pubDate") or "").strip()
            if not title or not link:
                continue
            items.append(
                WebSearchItem(
                    title=title,
                    url=link,
                    snippet=description,
                    published_at=self._normalize_rss_date(pub_date_raw),
                    source="google_news_rss",
                )
            )

        return WebSearchResult(
            query=query,
            provider="google_news_rss",
            items=items,
        )

    def _normalize_rss_date(self, raw: str) -> str | None:
        if not raw:
            return None
        try:
            dt = parsedate_to_datetime(raw)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            return None

    def _normalize_date(self, raw: object) -> str | None:
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            # Keep raw ISO-ish strings as-is if parse fails.
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except ValueError:
                return text
        return None

