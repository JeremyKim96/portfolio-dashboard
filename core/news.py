"""시장 뉴스 — 미국 증시에 영향을 주는 주요 뉴스 수집.

여러 금융 RSS 피드를 통합하여 최신순 top N 헤드라인 + 요약 + 출처 + 링크 반환.
신규 의존성 없이 requests + stdlib xml 으로 파싱.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests
import streamlit as st

from .config import PRICE_CACHE_TTL

# 미국 증시 중심 RSS 피드 (전부 무료, 키 불필요)
RSS_FEEDS = [
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch 시장", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("CNBC 시장", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("CNBC 경제", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
]

# 미국 증시 영향도 높은 키워드 (랭킹 가중)
_IMPACT_KEYWORDS = [
    "fed", "federal reserve", "powell", "rate", "inflation", "cpi", "pce",
    "jobs", "payroll", "unemployment", "gdp", "recession", "tariff", "trade",
    "nvidia", "ai", "chip", "semiconductor", "tech", "nasdaq", "s&p", "dow",
    "earnings", "treasury", "yield", "oil", "china", "tesla", "apple",
    "microsoft", "google", "rally", "selloff", "crash", "record", "bond",
]

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub("", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return " ".join(text.split()).strip()


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _impact_score(title: str, summary: str) -> int:
    text = (title + " " + summary).lower()
    return sum(1 for kw in _IMPACT_KEYWORDS if kw in text)


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def _fetch_feed(name: str, url: str) -> list[dict]:
    items = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []

    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        desc = _clean(it.findtext("description"))
        pub = _parse_date(it.findtext("pubDate"))
        if not title or not link:
            continue
        items.append({
            "source": name,
            "title": title,
            "summary": desc[:240],
            "link": link,
            "published": pub,
        })
    return items


_META_DESC_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|description|twitter:description)["\'][^>]*content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_DESC_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:description|description|twitter:description)["\']',
    re.IGNORECASE,
)


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_article_summary(url: str) -> str:
    """링크 페이지의 메타 설명(og:description 등)을 가져와 기사 요약으로 사용.

    RSS 요약이 비어 있을 때 보조로 사용. 실패 시 빈 문자열.
    """
    if not url:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(url, headers=headers, timeout=7)
        r.raise_for_status()
        html = r.text[:60000]  # 상단만 (메타태그는 head에 있음)
    except Exception:
        return ""
    m = _META_DESC_RE.search(html) or _META_DESC_RE2.search(html)
    if not m:
        return ""
    return _clean(m.group(1))[:240]


@st.cache_data(ttl=86400, show_spinner=False)
def translate_ko(text: str) -> str:
    """영문 → 한국어 (구글 무료 엔드포인트, 키 불필요). 실패 시 원문."""
    if not text or not text.strip():
        return ""
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": text},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
        return "".join(seg[0] for seg in data[0] if seg and seg[0]) or text
    except Exception:
        return text


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def get_market_news(top_n: int = 10) -> list[dict]:
    """미국 증시 영향 뉴스 top N. 최신성 + 영향키워드 가중 랭킹 + 한국어 번역.

    각 항목: {source, title, title_ko, summary, summary_ko, link, published, impact}
    """
    all_items: list[dict] = []
    seen_titles: set[str] = set()

    for name, url in RSS_FEEDS:
        for it in _fetch_feed(name, url):
            key = it["title"].lower()[:60]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            it["impact"] = _impact_score(it["title"], it["summary"])
            all_items.append(it)

    if not all_items:
        return []

    now = datetime.now(timezone.utc)

    def rank_key(it: dict) -> float:
        pub = it.get("published")
        hours_old = ((now - pub).total_seconds() / 3600) if pub else 72.0
        recency = max(0.0, 48 - hours_old) / 48  # 0~1 (48시간 이내 가중)
        return it["impact"] * 1.0 + recency * 3.0

    ranked = sorted(all_items, key=rank_key, reverse=True)[:top_n]

    # 상위 N개만: 요약 보강(빈 경우 링크 메타) + 한국어 번역
    for it in ranked:
        summary = it.get("summary", "")
        if not summary or len(summary) < 30:
            fetched = fetch_article_summary(it["link"])
            if fetched:
                summary = fetched
        it["summary"] = summary
        it["title_ko"] = translate_ko(it["title"])
        it["summary_ko"] = translate_ko(summary) if summary else ""

    return ranked


def news_sentiment(items: list[dict]) -> dict:
    """뉴스 헤드라인 기반 단순 시장 심리 (긍/부정 키워드 비율).

    버블 판독기의 한 입력으로도 사용. 반환: {score(-1~1), pos, neg, n}
    """
    POS = ["rally", "record", "surge", "gain", "rise", "jump", "soar", "beat",
           "high", "boom", "optimism", "upgrade", "bullish", "growth"]
    NEG = ["selloff", "crash", "fall", "drop", "plunge", "slump", "fear", "miss",
           "recession", "warning", "downgrade", "bearish", "loss", "cut", "tariff"]
    pos = neg = 0
    for it in items:
        t = (it["title"] + " " + it.get("summary", "")).lower()
        pos += sum(1 for w in POS if w in t)
        neg += sum(1 for w in NEG if w in t)
    total = pos + neg
    score = (pos - neg) / total if total else 0.0
    return {"score": score, "pos": pos, "neg": neg, "n": len(items)}
