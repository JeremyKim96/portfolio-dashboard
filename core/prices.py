from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import requests
import streamlit as st
import yfinance as yf

from .config import FX_CACHE_TTL, PRICE_CACHE_TTL, UPBIT_TICKER_URL, USD_KRW_TICKER


@dataclass
class StockQuote:
    ticker: str
    price: float | None
    prev_close: float | None
    week_ago_close: float | None
    currency: str | None
    quote_type: str | None = None  # "ETF" / "EQUITY"
    month_ago_close: float | None = None
    ytd_open: float | None = None  # 연초 첫 거래일 종가
    high_52w: float | None = None
    low_52w: float | None = None
    ma50: float | None = None
    ma200: float | None = None
    rsi14: float | None = None
    volatility_30d: float | None = None  # 30일 일별 수익률 표준편차 × √252


@dataclass
class CoinQuote:
    market: str
    price: float | None
    prev_close: float | None
    week_ago_close: float | None
    month_ago_close: float | None = None
    ytd_open: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    rsi14: float | None = None


@st.cache_data(ttl=FX_CACHE_TTL, show_spinner=False)
def get_usd_krw() -> float:
    """USD/KRW 환율. 1분 캐시 (실시간감)."""
    t = yf.Ticker(USD_KRW_TICKER)
    info = t.fast_info
    price = getattr(info, "last_price", None)
    if price:
        return float(price)
    hist = t.history(period="5d", auto_adjust=False)
    if hist.empty:
        raise RuntimeError("환율 조회 실패: yfinance KRW=X 응답 없음")
    return float(hist["Close"].iloc[-1])


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def get_stock_quote(ticker: str) -> StockQuote:
    t = yf.Ticker(ticker)
    hist = t.history(period="1y", auto_adjust=False)
    if hist.empty:
        return StockQuote(ticker=ticker, price=None, prev_close=None,
                          week_ago_close=None, currency=None)

    closes = hist["Close"].dropna()
    price = float(closes.iloc[-1]) if len(closes) >= 1 else None
    prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
    week_ago = float(closes.iloc[-6]) if len(closes) >= 6 else (
        float(closes.iloc[0]) if len(closes) >= 1 else None
    )
    month_ago = float(closes.iloc[-22]) if len(closes) >= 22 else (
        float(closes.iloc[0]) if len(closes) >= 1 else None
    )

    ytd_open = _ytd_open_from_series(closes)

    currency = None
    quote_type = None
    try:
        info = t.fast_info
        currency = getattr(info, "currency", None)
        quote_type = getattr(info, "quote_type", None)
    except Exception:
        pass

    high_52w = float(closes.max())
    low_52w = float(closes.min())
    ma50 = float(closes.tail(50).mean()) if len(closes) >= 20 else None
    ma200 = float(closes.tail(200).mean()) if len(closes) >= 60 else None
    rsi14 = _rsi(closes, 14)
    vol30 = _annualized_vol(closes.tail(30))

    return StockQuote(ticker=ticker, price=price, prev_close=prev,
                      week_ago_close=week_ago, currency=currency,
                      quote_type=quote_type,
                      month_ago_close=month_ago, ytd_open=ytd_open,
                      high_52w=high_52w, low_52w=low_52w,
                      ma50=ma50, ma200=ma200, rsi14=rsi14,
                      volatility_30d=vol30)


def _rsi(closes, period: int = 14) -> float | None:
    """Wilder RSI. closes는 pandas Series."""
    try:
        if len(closes) < period + 1:
            return None
        delta = closes.diff().dropna()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - 100 / (1 + rs))
    except Exception:
        return None


def _annualized_vol(closes) -> float | None:
    """일별 수익률 stdev × √252."""
    try:
        if len(closes) < 5:
            return None
        rets = closes.pct_change().dropna()
        if rets.empty:
            return None
        return float(rets.std() * (252 ** 0.5))
    except Exception:
        return None


def _ytd_open_from_series(closes) -> float | None:
    """올해 첫 거래일 종가. 데이터에 없으면 None."""
    try:
        idx = closes.index
        this_year = date.today().year
        mask = idx.year == this_year
        sliced = closes[mask]
        if sliced.empty:
            return None
        return float(sliced.iloc[0])
    except Exception:
        return None


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def get_coin_quote(market: str) -> CoinQuote:
    try:
        resp = requests.get(UPBIT_TICKER_URL, params={"markets": market}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return CoinQuote(market=market, price=None, prev_close=None, week_ago_close=None)

    if not data:
        return CoinQuote(market=market, price=None, prev_close=None, week_ago_close=None)

    row = data[0]
    price = float(row.get("trade_price")) if row.get("trade_price") is not None else None
    prev = float(row.get("prev_closing_price")) if row.get("prev_closing_price") is not None else None

    hist = _coin_history(market)
    return CoinQuote(market=market, price=price, prev_close=prev,
                     week_ago_close=hist.get("week_ago"),
                     month_ago_close=hist.get("month_ago"),
                     ytd_open=hist.get("ytd_open"),
                     high_52w=hist.get("high_52w"),
                     low_52w=hist.get("low_52w"),
                     rsi14=hist.get("rsi14"))


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def _coin_history(market: str) -> dict:
    """업비트 일봉 200개 → 주간/월간/YTD/52주/RSI."""
    try:
        resp = requests.get(
            "https://api.upbit.com/v1/candles/days",
            params={"market": market, "count": 200},
            timeout=5,
        )
        resp.raise_for_status()
        candles = resp.json()
    except Exception:
        return {}

    if not candles:
        return {}

    # 업비트 응답은 최신 → 과거 순. 즉 candles[0] = 오늘(또는 가장 최근)
    out: dict = {}
    if len(candles) > 5:
        out["week_ago"] = float(candles[5]["trade_price"])
    if len(candles) > 22:
        out["month_ago"] = float(candles[22]["trade_price"])

    # YTD: 가장 과거의 candle 중 올해 첫 거래일 종가
    this_year = date.today().year
    year_candles = [c for c in candles
                    if str(c.get("candle_date_time_kst", ""))[:4] == str(this_year)]
    if year_candles:
        out["ytd_open"] = float(year_candles[-1]["trade_price"])  # 가장 과거 = 연초

    # 52주 high/low (코인은 200일 데이터로 근사)
    prices = [float(c["trade_price"]) for c in candles if c.get("trade_price")]
    if prices:
        out["high_52w"] = max(prices)
        out["low_52w"] = min(prices)

    # RSI14 — 과거→최신 순서로 정렬해서 계산
    try:
        import pandas as pd
        s = pd.Series(list(reversed(prices)))
        rsi = _rsi(s, 14)
        if rsi is not None:
            out["rsi14"] = rsi
    except Exception:
        pass

    return out


def warm_stock_cache(tickers: Iterable[str]) -> None:
    for t in tickers:
        get_stock_quote(t)


def warm_coin_cache(markets: Iterable[str]) -> None:
    for m in markets:
        get_coin_quote(m)
