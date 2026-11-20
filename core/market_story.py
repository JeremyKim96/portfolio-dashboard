"""'왜 움직였나' — 오늘/이번주/이번달 시장 등락 + 원인 이슈 자동 정리.

1) 주요 자산 기간 등락 (S&P·나스닥·반도체지수·VIX·미국채10년·달러·BTC·ETH·환율·금·유가)
2) 한국어 뉴스 헤드라인 (구글뉴스 RSS, 기간 필터 when:1d/7d/30d) 수집
3) 키워드 기반 '원인 테마' 분류 → 건수·하락/상승 톤·대표 헤드라인

모두 공개 데이터. 실패 시 빈 결과 반환 (대시보드 영향 없음).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import pandas as pd
import requests
import streamlit as st

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

PERIODS = {"오늘": 1, "이번주": 7, "이번달": 30}

# (표시명, 심볼, 단위) — 단위 "pct" 퍼센트, "bp" 금리 bp, "pt" 지수 포인트(VIX)
STORY_SYMBOLS = [
    ("S&P500", "^GSPC", "pct"), ("나스닥", "^IXIC", "pct"), ("반도체지수", "^SOX", "pct"),
    ("VIX(공포)", "^VIX", "pt"), ("미 국채10년", "^TNX", "bp"), ("달러인덱스", "DX-Y.NYB", "pct"),
    ("비트코인", "BTC-USD", "pct"), ("이더리움", "ETH-USD", "pct"), ("원/달러", "KRW=X", "pct"),
    ("금", "GC=F", "pct"), ("WTI 유가", "CL=F", "pct"),
]


@st.cache_data(ttl=600, show_spinner=False)
def _closes() -> dict[str, list]:
    """심볼별 최근 3개월 종가 [(iso날짜, 종가)]. 캐시 직렬화 위해 list 로."""
    import yfinance as yf
    syms = [s for _, s, _ in STORY_SYMBOLS]
    out: dict[str, list] = {}
    try:
        df = yf.download(syms, period="3mo", interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    except Exception:
        df = None
    for s in syms:
        try:
            ser = df[s]["Close"].dropna() if df is not None else pd.Series(dtype=float)
        except Exception:
            ser = pd.Series(dtype=float)
        if ser.empty:
            try:
                ser = yf.Ticker(s).history(period="3mo", auto_adjust=False)["Close"].dropna()
            except Exception:
                continue
        idx = pd.to_datetime(ser.index)
        try:
            idx = idx.tz_localize(None)
        except (TypeError, AttributeError):
            pass
        out[s] = [(d.strftime("%Y-%m-%d"), float(v)) for d, v in zip(idx, ser.values)]
    return out


def period_moves(period: str) -> list[dict]:
    """기간별 등락. 오늘=직전 거래일 대비, 이번주=7일 전, 이번달=30일 전 종가 대비."""
    days = PERIODS.get(period, 1)
    data = _closes()
    out = []
    for label, sym, unit in STORY_SYMBOLS:
        rows = data.get(sym)
        if not rows or len(rows) < 2:
            continue
        ser = pd.Series({pd.Timestamp(d): v for d, v in rows}).sort_index()
        last_d, last = ser.index[-1], float(ser.iloc[-1])
        if days == 1:
            base_d, base = ser.index[-2], float(ser.iloc[-2])
        else:
            prior = ser[ser.index <= last_d - pd.Timedelta(days=days)]
            if prior.empty:
                continue
            base_d, base = prior.index[-1], float(prior.iloc[-1])
        if base == 0:
            continue
        pct = last / base - 1
        out.append({"label": label, "symbol": sym, "unit": unit, "last": last,
                    "base": base, "pct": pct, "diff": last - base,
                    "last_date": last_d.strftime("%m/%d"), "base_date": base_d.strftime("%m/%d")})
    return out


# ─────────────────────────────────────────────────────────────
#  한국어 헤드라인 + 원인 테마
# ─────────────────────────────────────────────────────────────
_QUERIES = ["뉴욕증시", "미국 반도체 주가", "비트코인 가격", "연준 금리", "나스닥 급락 OR 급등",
            "이더리움 가격"]

THEMES: list[tuple[str, str, list[str]]] = [
    ("ai_semi", "🤖 AI·반도체", ["반도체", "엔비디아", "AI", "마이크론", "TSMC", "브로드컴", "AMD",
                              "HBM", "앤트로픽", "엔트로픽", "앤스로픽", "오픈AI", "메모리", "필라델피아",
                              "SOX", "칩", "데이터센터"]),
    ("rates", "🏦 금리·연준", ["연준", "금리", "FOMC", "워시", "파월", "국채", "인상", "인하", "Fed",
                            "통화정책", "긴축"]),
    ("macro", "🔥 물가·경제지표", ["CPI", "물가", "인플레", "고용", "실업", "소매판매", "PCE", "GDP",
                              "PPI", "경기침체", "소비"]),
    ("crypto", "🪙 코인", ["비트코인", "이더리움", "가상자산", "가상화폐", "코인", "청산", "알트",
                         "스트래티지", "코인베이스", "현물 ETF"]),
    ("policy", "🌏 정책·지정학", ["트럼프", "관세", "수출통제", "수출 통제", "중국", "제재", "전쟁",
                              "백악관", "규제", "셧다운"]),
    ("earnings", "💼 기업 실적", ["실적", "어닝", "매출", "가이던스", "영업이익", "분기"]),
    ("bigtech", "🏢 빅테크", ["테슬라", "애플", "마이크로소프트", "구글", "알파벳", "아마존", "메타",
                           "오라클", "팔란티어", "M7", "매그니피센트"]),
]
_DOWN = ["급락", "폭락", "하락", "약세", "투매", "붕괴", "추락", "털썩", "뚝", "공포", "↓", "급감",
         "최저", "손실", "청산", "매도세", "조정", "패닉"]
_UP = ["급등", "상승", "반등", "강세", "랠리", "최고", "↑", "회복", "폭등", "신고가", "훈풍"]


def _split_source(title: str) -> tuple[str, str]:
    if " - " in title:
        t, src = title.rsplit(" - ", 1)
        return t.strip(), src.strip()
    return title.strip(), ""


def _gnews(query: str, when: str) -> list[dict]:
    try:
        r = requests.get("https://news.google.com/rss/search",
                         params={"q": f"{query} when:{when}", "hl": "ko", "gl": "KR",
                                 "ceid": "KR:ko"},
                         headers=_UA, timeout=8)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for it in list(root.iter("item"))[:40]:
        raw_title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not raw_title or not link:
            continue
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "")
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except Exception:
            pub = None
        title, src = _split_source(raw_title)
        src_el = it.find("source")
        if src_el is not None and (src_el.text or "").strip():
            src = src_el.text.strip()
        out.append({"title": title, "source": src, "link": link,
                    "published": pub.isoformat() if pub else None})
    return out


_NORM_RE = re.compile(r"[^0-9A-Za-z가-힣]")


@st.cache_data(ttl=1800, show_spinner=False)
def korean_headlines(period: str) -> list[dict]:
    days = PERIODS.get(period, 1)
    when = {1: "1d", 7: "7d", 30: "30d"}[days]
    with ThreadPoolExecutor(max_workers=6) as ex:
        batches = list(ex.map(lambda q: _gnews(q, when), _QUERIES))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days, hours=12)
    seen, out = set(), []
    for batch in batches:
        for it in batch:
            key = _NORM_RE.sub("", it["title"])[:28]
            if not key or key in seen:
                continue
            if it["published"]:
                if datetime.fromisoformat(it["published"]) < cutoff:
                    continue
            seen.add(key)
            out.append(it)
    out.sort(key=lambda x: x["published"] or "", reverse=True)
    return out


def _tone(text: str) -> int:
    d = sum(1 for w in _DOWN if w in text)
    u = sum(1 for w in _UP if w in text)
    return (u > d) - (d > u)


def theme_breakdown(headlines: list[dict], top_n: int = 5, per_theme: int = 4) -> list[dict]:
    """헤드라인 → 원인 테마별 {key, label, count, down, up, tone, items}."""
    buckets: dict[str, dict] = {}
    for key, label, kws in THEMES:
        buckets[key] = {"key": key, "label": label, "count": 0, "down": 0, "up": 0, "items": []}
    for h in headlines:
        title = h["title"]
        low = title.lower()
        tone = _tone(title)
        for key, label, kws in THEMES:
            if any((k.lower() in low) if k.isascii() else (k in title) for k in kws):
                b = buckets[key]
                b["count"] += 1
                if tone < 0:
                    b["down"] += 1
                elif tone > 0:
                    b["up"] += 1
                if len(b["items"]) < 40:
                    b["items"].append(h)
    res = [b for b in buckets.values() if b["count"] > 0]
    for b in res:
        b["tone"] = ("하락 우세" if b["down"] > b["up"] * 1.3 else
                     "상승 우세" if b["up"] > b["down"] * 1.3 else "혼조")
    res.sort(key=lambda b: b["count"], reverse=True)
    res = res[:top_n]
    # 같은 헤드라인이 여러 테마 카드에 반복되지 않게 (건수 많은 테마부터 배정)
    shown: set[str] = set()
    for b in res:
        picked = [h for h in b["items"] if h["link"] not in shown][:per_theme]
        shown.update(h["link"] for h in picked)
        b["items"] = picked
    return res


def fmt_move(m: dict) -> str:
    if m["unit"] == "bp":
        return f"{m['diff'] * 100:+.0f}bp"
    if m["unit"] == "pt":
        return f"{m['diff']:+.1f}p"
    return f"{m['pct'] * 100:+.1f}%"


def fmt_level(m: dict) -> str:
    v = m["last"]
    if m["unit"] == "bp":
        return f"{v:.2f}%"
    if m["symbol"] in ("BTC-USD",):
        return f"${v:,.0f}"
    if m["symbol"] in ("ETH-USD", "GC=F", "CL=F"):
        return f"${v:,.0f}" if v >= 1000 else f"${v:,.2f}"
    if m["symbol"] == "KRW=X":
        return f"{v:,.1f}원"
    return f"{v:,.2f}" if v < 100 else f"{v:,.0f}"


def story_summary(period: str, moves: list[dict], themes: list[dict]) -> str:
    """한 줄 자동 요약."""
    pick = {m["symbol"]: m for m in moves}
    parts = []
    for sym, name in [("^IXIC", "나스닥"), ("^SOX", "반도체"), ("BTC-USD", "비트코인")]:
        if sym in pick:
            parts.append(f"{name} {fmt_move(pick[sym])}")
    head = " · ".join(parts)
    if themes:
        th = ", ".join(f"{t['label']}({t['count']}건·{t['tone']})" for t in themes[:3])
        return f"{period} {head} — 주요 원인 테마: {th}"
    return f"{period} {head}"
