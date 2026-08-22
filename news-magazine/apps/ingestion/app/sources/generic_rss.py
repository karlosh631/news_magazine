"""
Generic RSS/Atom adapter.

This is deliberately the ONLY adapter shipped out of the box. Per the
spec's own rule ("do not claim a source is supported until its permitted
feed/API/parser has been verified"), we are not hard-coding
site-specific scrapers for Ekantipur / 24 News / Online Taja Khabar here,
because that requires an administrator to first confirm each site's
robots.txt and Terms of Service actually permit automated ingestion, and
to obtain the real feed URL.

Instead: an admin adds a `sources` row with adapter_key='generic_rss' and
a feed_url pointing at that outlet's own published RSS/Atom feed (most
Nepali outlets do publish one), sets allowed=true only after verifying
the legal points above, and this adapter takes it from there. If a given
outlet turns out not to offer a permitted feed, leave the source
disabled — do not write a scraper to work around that.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

import feedparser
import httpx

from app.sources.base import NormalizedArticle, SourceAdapter


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip().lower()


def _content_hash(canonical_url: str, title_normalized: str) -> str:
    return hashlib.sha256(f"{canonical_url}|{title_normalized}".encode("utf-8")).hexdigest()


class GenericRssAdapter(SourceAdapter):
    async def fetch(self) -> str:
        feed_url = self.source["feed_url"]
        if not await self.check_allowed(feed_url):
            raise PermissionError(
                f"Source '{self.source['name']}' is not marked allowed=true and "
                f"robots.txt-verified; refusing to fetch {feed_url}."
            )
        await self.rate_limiter.wait()
        headers = {"User-Agent": self.settings.default_user_agent}
        async with httpx.AsyncClient(timeout=self.settings.default_request_timeout_seconds) as client:
            resp = await client.get(feed_url, headers=headers)
            resp.raise_for_status()
            return resp.text

    def parse(self, raw: str) -> list[dict]:
        parsed = feedparser.parse(raw)
        return [dict(entry) for entry in parsed.entries]

    def normalize(self, entries: list[dict]) -> list[NormalizedArticle]:
        out: list[NormalizedArticle] = []
        for e in entries:
            link = e.get("link")
            title = e.get("title")
            if not link or not title:
                continue

            published = None
            if e.get("published_parsed"):
                published = datetime(*e["published_parsed"][:6], tzinfo=timezone.utc).isoformat()

            # Only ever keep a permitted excerpt/summary — never scrape or
            # store more than the feed itself provides, and never treat a
            # feed <summary> as full body content.
            excerpt = e.get("summary")
            if excerpt:
                excerpt = re.sub("<[^<]+?>", "", excerpt).strip()  # strip any embedded HTML

            thumbnail = None
            media_content = e.get("media_content") or e.get("media_thumbnail")
            if media_content and isinstance(media_content, list):
                thumbnail = media_content[0].get("url")

            title_normalized = _normalize_title(title)
            out.append(
                NormalizedArticle(
                    source_article_url=link,
                    canonical_url=link.split("?")[0],
                    headline=title.strip(),
                    published_at=published,
                    author_name=e.get("author"),
                    excerpt=excerpt,
                    thumbnail_url=thumbnail,
                    full_body_html=None,  # generic_rss never republishes full text
                    language=self.source.get("default_language", "ne"),
                    raw_metadata={
                        "content_hash": _content_hash(link.split("?")[0], title_normalized),
                        "title_normalized": title_normalized,
                    },
                )
            )
        return out
