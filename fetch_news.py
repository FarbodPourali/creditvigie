"""Collect recent headlines for each issuer from Google News RSS (French and English editions)."""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

EDITIONS = [
    {"hl": "fr", "gl": "FR", "ceid": "FR:fr"},
    {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"},
]
MAX_HEADLINES_PER_ISSUER = 40
USER_AGENT = "Mozilla/5.0 (veille-credit demo)"
# Quote pages and ISIN pages carry no news; dropping them leaves room for real headlines.
NOISE = re.compile(
    r"^[A-Z]{2}[A-Z0-9]{9}\d\b|Stock Price, News, Quote|Latest News and Headlines|Seasonals Chart|Bond Yield and Price",
    re.IGNORECASE,
)


def _rss_url(query: str, days: int, edition: dict) -> str:
    params = {"q": f"{query} when:{days}d", **edition}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def _parse_feed(xml_bytes: bytes) -> list[dict]:
    items = []
    root = ET.fromstring(xml_bytes)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        # Google appends " - Source" to every title; drop it, the source has its own field.
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        pub = item.findtext("pubDate")
        try:
            published = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            published = None
        items.append({
            "titre": title,
            "source": source,
            "url": (item.findtext("link") or "").strip(),
            "date": published.astimezone(timezone.utc).isoformat() if published else None,
        })
    return items


def fetch_issuer_news(issuer: dict, days: int) -> list[dict]:
    """Return de-duplicated headlines for one issuer, newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen = set()
    headlines = []
    for query in issuer["requetes"]:
        for edition in EDITIONS:
            req = urllib.request.Request(_rss_url(query, days, edition), headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    items = _parse_feed(resp.read())
            except Exception as exc:  # one bad feed should not stop the run
                print(f"  ! flux ignoré ({query}, {edition['hl']}): {exc}")
                continue
            for it in items:
                key = it["titre"].lower()
                if not it["titre"] or key in seen or NOISE.search(it["titre"]):
                    continue
                if it["date"] and datetime.fromisoformat(it["date"]) < cutoff:
                    continue
                seen.add(key)
                headlines.append(it)
    headlines.sort(key=lambda h: h["date"] or "", reverse=True)
    return headlines[:MAX_HEADLINES_PER_ISSUER]
