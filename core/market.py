"""시장 컨텍스트 (VIX, 주요 지수, 환율, CNN Fear & Greed).

대시보드 상단 카드용. 실패 시 빈 dict 반환 → 메인 대시보드 영향 없음.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests
import streamlit as st
import yfinance as yf

from .config import PRICE_CACHE_TTL


@dataclass
class MarketTicker:
    key: str            # 내부 키 ("vix", "spx", "ndx", ...)
    label: str          # 표시 이름 ("VIX", "S&P 500", ...)
    yf_symbol: str      # yfinance 심볼
    fmt: str            # 표시 포맷 ("{:,.2f}", "{:,.0f}")


MARKET_TICKERS: tuple[MarketTicker, ...] = (
    MarketTicker("vix",    "VIX 변동성",  "^VIX",     "{:,.2f}"),
    MarketTicker("spx",    "S&P 500",     "^GSPC",    "{:,.2f}"),
    MarketTicker("ndx",    "나스닥종합",  "^IXIC",    "{:,.2f}"),
    MarketTicker("spy",    "SPY",         "SPY",      "{:,.2f}"),
    MarketTicker("qqq",    "QQQ",         "QQQ",      "{:,.2f}"),
    MarketTicker("soxl",   "SOXL (반도체3X)", "SOXL", "{:,.2f}"),
    MarketTicker("btc",    "BTC/USD",     "BTC-USD",  "{:,.0f}"),
    MarketTicker("eth",    "ETH/USD",     "ETH-USD",  "{:,.2f}"),
    MarketTicker("usdkrw", "USD/KRW",     "KRW=X",    "{:,.2f}"),
)


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def _fetch_one(symbol: str) -> dict:
    """단일 심볼 10일치 종가에서 현재/전일/주간 비교."""
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="10d", auto_adjust=False)
        if hist.empty:
            return {}
        closes = hist["Close"].dropna()
        if closes.empty:
            return {}
        price = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
        week_ago = float(closes.iloc[-6]) if len(closes) >= 6 else (
            float(closes.iloc[0]) if len(closes) >= 1 else None
        )
        day_change = ((price - prev) / prev) if prev and prev > 0 else 0.0
        week_change = ((price - week_ago) / week_ago) if week_ago and week_ago > 0 else 0.0
        return {
            "price": price,
            "prev_close": prev,
            "week_ago_close": week_ago,
            "day_change": day_change,
            "week_change": week_change,
        }
    except Exception:
        return {}


def get_market_dashboard() -> dict[str, dict]:
    """전체 시장 패널. {key: {label, fmt, price, day_change, week_change}}.

    개별 심볼 실패는 무시하고 가능한 것만 반환.
    """
    out: dict[str, dict] = {}
    for t in MARKET_TICKERS:
        data = _fetch_one(t.yf_symbol)
        if not data:
            continue
        out[t.key] = {
            "label": t.label,
            "fmt": t.fmt,
            "symbol": t.yf_symbol,
            **data,
        }
    return out


def vix_color(vix: float) -> str:
    """VIX 색상 코딩 (Streamlit metric delta_color 호환 아님 → 텍스트용)."""
    if vix >= 30:
        return "🔴"
    if vix >= 20:
        return "🟡"
    return "🟢"


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def get_market_regime(symbol: str = "^GSPC") -> dict:
    """시장 국면 진단 (S&P500 50일·200일 이평선).

    반환: {price, ma50, ma200, status, vs_ma50_pct, vs_ma200_pct}
    status: '강세(골든크로스)' / '약세(데드크로스)' / '중립'
    """
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="1y", auto_adjust=False)
        if hist.empty or len(hist) < 50:
            return {}
        closes = hist["Close"].dropna()
        price = float(closes.iloc[-1])
        ma50 = float(closes.tail(50).mean())
        ma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else None

        if ma200 is None:
            status = "데이터 부족"
        elif ma50 > ma200 and price > ma50:
            status = "🟢 강세 (골든크로스)"
        elif ma50 < ma200 and price < ma50:
            status = "🔴 약세 (데드크로스)"
        else:
            status = "🟡 중립"

        return {
            "price": price,
            "ma50": ma50,
            "ma200": ma200,
            "status": status,
            "vs_ma50_pct": (price - ma50) / ma50 if ma50 else 0.0,
            "vs_ma200_pct": (price - ma200) / ma200 if ma200 else 0.0,
        }
    except Exception:
        return {}


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def get_fear_and_greed() -> dict:
    """CNN Fear & Greed Index. 비공식 production.dataviz endpoint 사용.

    반환: {score(0-100), rating, prev_close, prev_week, prev_month, prev_year, label, color, advice}
    실패 시 빈 dict.
    """
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}

    fg = data.get("fear_and_greed", {})
    if not fg:
        return {}
    try:
        score = float(fg.get("score", 0))
    except (TypeError, ValueError):
        return {}

    rating_raw = str(fg.get("rating", "")).lower()
    if score < 25:
        label, color, advice = "🔴 극단적 공포", "red", "역사적 저점 가능성. 분할 매수 적기."
    elif score < 45:
        label, color, advice = "🟠 공포", "orange", "약세 심리. 우량주 분할 매수 고려."
    elif score < 55:
        label, color, advice = "⚪ 중립", "gray", "방향성 약함. 기존 비중 유지."
    elif score < 75:
        label, color, advice = "🟢 탐욕", "green", "강세 심리. 익절 일부 고려."
    else:
        label, color, advice = "🔴 극단적 탐욕", "red", "과열 구간. 신규 진입 신중·익절 적극."

    return {
        "score": score,
        "rating": rating_raw,
        "prev_close": _safe(fg.get("previous_close")),
        "prev_week": _safe(fg.get("previous_1_week")),
        "prev_month": _safe(fg.get("previous_1_month")),
        "prev_year": _safe(fg.get("previous_1_year")),
        "label": label,
        "color": color,
        "advice": advice,
    }


def _safe(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def market_temperature(vix: float | None, regime_status: str) -> dict:
    """투자 컨디션 종합 평가 → {label, color, advice}."""
    if vix is None:
        return {"label": "데이터 없음", "color": "gray", "advice": ""}

    bullish = "강세" in regime_status
    bearish = "약세" in regime_status

    if vix < 15 and bullish:
        return {"label": "🟢 공격 가능",  "advice": "변동성 낮음 + 강세장. 레버리지 추가 부담 낮음."}
    if vix < 20 and bullish:
        return {"label": "🟢 양호",      "advice": "안정적인 상승. 분할매수·신규 진입 적정."}
    if 20 <= vix < 30 and not bearish:
        return {"label": "🟡 주의",      "advice": "변동성 확대. 신규 레버리지 진입 신중."}
    if vix >= 30:
        return {"label": "🔴 위험",      "advice": "공포 구간. 현금 비중 점검·헷지 고려."}
    if bearish:
        return {"label": "🔴 약세",      "advice": "추세적 하락. 방어 자세, 평단가 분할 매수만."}
    return {"label": "🟡 중립", "advice": "특이 시그널 없음. 기존 비중 유지."}
