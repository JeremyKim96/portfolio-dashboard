"""'앞으로' — 시장 신호판 + 과거 유사 상황 통계 + 시나리오.

예측이 아니라 '데이터가 지금 어디를 가리키는지'와 '과거 비슷한 상황 뒤 실제로
어떻게 됐는지'를 보여주는 판단 보조 도구. 투자 자문 아님.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import streamlit as st

_SIG_SYMS = ["^GSPC", "^IXIC", "^SOX", "^VIX", "^VIX3M", "BTC-USD", "ETH-USD", "^TNX",
             "QQQ", "SOXX", "KRW=X"]


def _series(df, sym) -> pd.Series:
    try:
        s = df[sym]["Close"].dropna()
    except Exception:
        return pd.Series(dtype=float)
    idx = pd.to_datetime(s.index)
    try:
        idx = idx.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    s.index = idx
    return s.astype(float)


@st.cache_data(ttl=1800, show_spinner=False)
def market_signals() -> dict:
    """1년치 일봉 → 추세·낙폭·변동성·기간구조 신호. 실패 항목은 없음."""
    import yfinance as yf
    try:
        df = yf.download(_SIG_SYMS, period="1y", interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    except Exception:
        return {}
    out: dict = {}

    def trend(sym, key):
        s = _series(df, sym)
        if len(s) < 60:
            return
        last = float(s.iloc[-1])
        ma50 = float(s.tail(50).mean())
        ma200 = float(s.tail(200).mean()) if len(s) >= 200 else None
        hi = float(s.tail(252).max())
        out[key] = {
            "last": last, "ma50": ma50, "ma200": ma200,
            "vs_ma50": last / ma50 - 1,
            "vs_ma200": (last / ma200 - 1) if ma200 else None,
            "dd": last / hi - 1, "high": hi,
            "chg_1m": last / float(s.iloc[-22]) - 1 if len(s) > 22 else None,
        }

    trend("^GSPC", "spx")
    trend("^IXIC", "ndx")
    trend("^SOX", "sox")
    trend("BTC-USD", "btc")
    trend("ETH-USD", "eth")

    vix, vix3m = _series(df, "^VIX"), _series(df, "^VIX3M")
    if not vix.empty:
        out["vix"] = float(vix.iloc[-1])
        out["vix_1y_avg"] = float(vix.mean())
    if not vix.empty and not vix3m.empty:
        out["vix_term"] = float(vix.iloc[-1]) / float(vix3m.iloc[-1])

    tnx = _series(df, "^TNX")
    if len(tnx) > 22:
        out["tnx"] = float(tnx.iloc[-1])
        out["tnx_1m_bp"] = (float(tnx.iloc[-1]) - float(tnx.iloc[-22])) * 100

    def vol(sym, days=20, ann=252):
        s = _series(df, sym)
        if len(s) < days + 2:
            return None
        r = np.log(s / s.shift(1)).dropna().tail(days)
        return float(r.std() * math.sqrt(ann))

    out["vol_qqq"] = vol("QQQ")
    out["vol_soxx"] = vol("SOXX")
    out["vol_eth"] = vol("ETH-USD", ann=365)

    fx = _series(df, "KRW=X")
    if len(fx) > 100:
        out["usdkrw"] = float(fx.iloc[-1])
        out["usdkrw_avg"] = float(fx.mean())
    return out


def leverage_decay(lev: float, sigma: float | None) -> float | None:
    """레버리지 ETF 변동성 끌림(이론) — 기초지수가 제자리일 때 1개월 기대 손실률.

    연간 로그 끌림 ≈ (L² − L)/2 · σ².  월 환산 = exp(−끌림/12) − 1
    """
    if not sigma or lev <= 1:
        return None
    drag = (lev * lev - lev) / 2 * sigma * sigma
    return math.exp(-drag / 12) - 1


# ─────────────────────────────────────────────────────────────
#  과거 유사 상황 (Analog) 통계
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def _long_history() -> dict[str, list]:
    import yfinance as yf
    out = {}
    for s in ["^GSPC", "^VIX", "^SOX", "BTC-USD", "^IXIC"]:
        try:
            h = yf.Ticker(s).history(period="max", auto_adjust=False)["Close"].dropna()
            idx = pd.to_datetime(h.index)
            try:
                idx = idx.tz_localize(None)
            except (TypeError, AttributeError):
                pass
            out[s] = [(d.strftime("%Y-%m-%d"), float(v)) for d, v in zip(idx, h.values)]
        except Exception:
            continue
    return out


def _to_series(rows) -> pd.Series:
    return pd.Series({pd.Timestamp(d): v for d, v in rows}).sort_index()


def _episodes(cond: pd.Series, gap: int = 60) -> list[pd.Timestamp]:
    """조건 True 인 날 중 직전 에피소드와 gap 거래일 이상 떨어진 첫날만."""
    days = list(cond.index)
    out, last_i = [], -10**9
    for i, (d, v) in enumerate(zip(days, cond.values)):
        if v and i - last_i >= gap:
            out.append(d)
            last_i = i
        elif v:
            last_i = i  # 연속 구간이면 갱신 (클러스터 안에서 새 에피소드 안 만듦)
    return out


def _forward(px: pd.Series, starts: list, horizons=(21, 63, 126)) -> dict:
    res = {h: [] for h in horizons}
    pos = {d: i for i, d in enumerate(px.index)}
    vals = px.values
    for d in starts:
        i = pos.get(d)
        if i is None:
            continue
        for h in horizons:
            if i + h < len(vals):
                res[h].append(vals[i + h] / vals[i] - 1)
    return res


def analog_studies(sig: dict) -> list[dict]:
    """현재 상황과 비슷한 과거 에피소드 → 이후 1/3/6개월 수익률 통계."""
    hist = _long_history()
    if not hist:
        return []
    studies = []

    def add(name, desc, cond_px: pd.Series, cond: pd.Series, target: pd.Series,
            target_name, active: bool, now_txt: str):
        cond = cond.reindex(target.index).fillna(False).astype(bool)
        eps = _episodes(cond)
        fw = _forward(target, eps)
        if not fw[63]:
            return
        stats = {}
        for h, lab in [(21, "1개월"), (63, "3개월"), (126, "6개월")]:
            arr = np.array(fw[h])
            if len(arr):
                stats[lab] = {"median": float(np.median(arr)), "win": float((arr > 0).mean()),
                              "worst": float(arr.min()), "best": float(arr.max()), "n": int(len(arr))}
        studies.append({"name": name, "desc": desc, "target": target_name, "active": active,
                        "now": now_txt, "n": len(eps), "stats": stats,
                        "recent": [d.strftime("%Y-%m") for d in eps[-5:]]})

    spx = _to_series(hist["^GSPC"]) if "^GSPC" in hist else None
    vix = _to_series(hist["^VIX"]) if "^VIX" in hist else None
    sox = _to_series(hist["^SOX"]) if "^SOX" in hist else None
    btc = _to_series(hist["BTC-USD"]) if "BTC-USD" in hist else None

    if spx is not None and vix is not None:
        cur = sig.get("vix")
        add("VIX 35 이상 (공포 급등)", "VIX가 35를 넘은 날 이후 S&P500",
            vix, vix >= 35, spx, "S&P500", bool(cur and cur >= 35),
            f"현재 VIX {cur:.1f}" if cur else "")
    if spx is not None:
        dd = spx / spx.rolling(252, min_periods=60).max() - 1
        cur = (sig.get("spx") or {}).get("dd")
        add("S&P500 고점 대비 -10% 조정", "52주 고점 대비 10% 이상 하락한 날 이후 S&P500",
            spx, dd <= -0.10, spx, "S&P500", bool(cur is not None and cur <= -0.095),
            f"현재 고점 대비 {cur*100:.1f}%" if cur is not None else "")
    if sox is not None:
        dd = sox / sox.rolling(252, min_periods=60).max() - 1
        cur = (sig.get("sox") or {}).get("dd")
        add("반도체지수 고점 대비 -25% 이상", "필라델피아 반도체지수(SOX) 25% 이상 하락 이후 SOX",
            sox, dd <= -0.25, sox, "반도체지수", bool(cur is not None and cur <= -0.25),
            f"현재 고점 대비 {cur*100:.1f}%" if cur is not None else "")
    if btc is not None:
        chg30 = btc / btc.shift(30) - 1
        cur = (sig.get("btc") or {}).get("chg_1m")
        add("비트코인 한 달 -25% 이상 급락", "BTC가 30일간 25% 이상 하락한 날 이후 BTC",
            btc, chg30 <= -0.25, btc, "비트코인", bool(cur is not None and cur <= -0.22),
            f"현재 1개월 {cur*100:.1f}%" if cur is not None else "")
    studies.sort(key=lambda s: not s["active"])
    return studies


# ─────────────────────────────────────────────────────────────
#  신호판 + 시나리오
# ─────────────────────────────────────────────────────────────
def signal_board(sig: dict, fg: dict | None) -> list[dict]:
    """[{name, value, state(🟢/🟡/🔴), meaning}] — 현재 시장이 가리키는 방향."""
    rows = []

    def add(name, value, state, meaning):
        rows.append({"name": name, "value": value, "state": state, "meaning": meaning})

    spx = sig.get("spx") or {}
    if spx.get("vs_ma200") is not None:
        v = spx["vs_ma200"]
        add("S&P500 vs 200일선", f"{v*100:+.1f}%", "🟢" if v > 0.02 else ("🟡" if v > -0.02 else "🔴"),
            "장기 추세선 위면 상승추세 유지, 아래면 약세장 경계")
    if spx.get("dd") is not None:
        v = spx["dd"]
        add("S&P500 고점 대비", f"{v*100:.1f}%", "🟢" if v > -0.05 else ("🟡" if v > -0.10 else "🔴"),
            "-10%↓ 조정, -20%↓ 약세장")
    sox = sig.get("sox") or {}
    if sox.get("dd") is not None:
        v = sox["dd"]
        add("반도체지수 고점 대비", f"{v*100:.1f}%", "🟢" if v > -0.10 else ("🟡" if v > -0.20 else "🔴"),
            "SOXL(3배)은 대략 이 하락폭의 3배 이상 영향")
    if sox.get("vs_ma200") is not None:
        v = sox["vs_ma200"]
        add("반도체 vs 200일선", f"{v*100:+.1f}%", "🟢" if v > 0.02 else ("🟡" if v > -0.03 else "🔴"),
            "200일선 회복 여부가 반도체 추세 전환의 1차 확인 신호")
    if sig.get("vix") is not None:
        v = sig["vix"]
        add("VIX 변동성", f"{v:.1f}", "🟢" if v < 20 else ("🟡" if v < 30 else "🔴"),
            "20↓ 안정 · 30↑ 공포. 극단적 급등은 단기 바닥 부근에서도 자주 나타남")
    if sig.get("vix_term") is not None:
        v = sig["vix_term"]
        add("VIX 기간구조 (단기/3개월)", f"{v:.2f}", "🟢" if v < 0.9 else ("🟡" if v < 1.0 else "🔴"),
            "1.0 초과(역전) = 당장의 공포가 미래보다 큼 → 패닉 국면. 1.0 아래로 복귀하면 진정 신호")
    if fg:
        v = fg.get("score")
        if v is not None:
            add("CNN 공포·탐욕", f"{v:.0f}", "🔴" if v < 25 or v > 75 else ("🟡" if v < 45 or v > 60 else "🟢"),
                "극단적 공포(25↓)·탐욕(75↑) 모두 변동성 큰 구간")
    if sig.get("tnx_1m_bp") is not None:
        v = sig["tnx_1m_bp"]
        add("미 국채10년 1개월 변화", f"{v:+.0f}bp ({sig.get('tnx', 0):.2f}%)",
            "🟢" if v < -10 else ("🟡" if v < 15 else "🔴"),
            "금리 급등은 기술주·성장주 밸류에이션에 부담")
    btc = sig.get("btc") or {}
    if btc.get("vs_ma200") is not None:
        v = btc["vs_ma200"]
        add("비트코인 vs 200일선", f"{v*100:+.1f}%", "🟢" if v > 0.02 else ("🟡" if v > -0.05 else "🔴"),
            "코인 장기 추세. 200일선 아래 장기화 시 약세장 성격")
    return rows


def board_verdict(rows: list[dict]) -> tuple[str, str]:
    red = sum(r["state"] == "🔴" for r in rows)
    green = sum(r["state"] == "🟢" for r in rows)
    n = max(len(rows), 1)
    if red / n >= 0.5:
        return "🔴 하락 압력 우세", f"신호 {n}개 중 위험 {red}개 · 양호 {green}개"
    if green / n >= 0.5:
        return "🟢 회복·상승 신호 우세", f"신호 {n}개 중 양호 {green}개 · 위험 {red}개"
    return "🟡 혼조 (방향 탐색)", f"신호 {n}개 중 위험 {red}개 · 양호 {green}개"


def scenarios(sig: dict, events: list[dict], stress: dict) -> list[dict]:
    """3가지 시나리오 카드 — 트리거(이런 일이 생기면) + 체크 지표 + 내 자산 영향(추정)."""
    key_ev = [e for e in events if e.get("imp", 0) >= 3 and e["cat"] in ("금리", "물가", "고용", "실적")][:5]
    ev_txt = " · ".join(f"{e['date'].month}/{e['date'].day} {e['title'].split('(')[0].strip()}"
                        for e in key_ev)
    vix = sig.get("vix")
    sox_ma = (sig.get("sox") or {}).get("ma200")
    return [
        {
            "name": "🟢 반등 (안도 랠리)",
            "triggers": ["물가·고용 지표 둔화 → 연준 금리 인상 우려 완화",
                         "반도체 실적에서 AI 수요 재확인 (가이던스 유지·상향)",
                         "VIX 30 아래 복귀 + 기간구조 정상화(1.0 미만)"],
            "watch": f"VIX {vix:.0f} → 30 아래" if vix else "VIX 30 아래 복귀",
            "impact": stress.get("반등", {}),
        },
        {
            "name": "🟡 기본 (높은 변동성 속 박스권)",
            "triggers": ["지표가 엇갈리며 '금리 인상 여부' 공방 지속",
                         "하루 ±2~3% 등락 반복, 방향 없이 출렁임",
                         "레버리지 ETF는 지수가 제자리여도 변동성 손실 누적"],
            "watch": "핵심 일정: " + (ev_txt or "—"),
            "impact": stress.get("박스권", {}),
        },
        {
            "name": "🔴 추가 하락",
            "triggers": ["물가 재가속 + 연준 금리 인상 → 국채금리 상승",
                         "AI 투자 회수 의구심 확산 · 반도체 가이던스 하향",
                         "코인 ETF 자금 유출·레버리지 청산 연쇄 지속"],
            "watch": (f"반도체지수 200일선({sox_ma:,.0f}) 회복 실패 지속" if sox_ma
                      else "반도체지수 200일선 회복 실패"),
            "impact": stress.get("복합 하락", {}),
        },
    ]


# ─────────────────────────────────────────────────────────────
#  Claude 시장 노트 (작성 시점 고정 — 이후 변화는 자동 뉴스/신호판 참고)
# ─────────────────────────────────────────────────────────────
CLAUDE_NOTE = {
    "date": "2026-11-21",
    "title": "11월 급락장 정리 — 무엇이 시장을 흔들었나",
    "points": [
        "**AI 투자 회수 의구심** — 앤트로픽 상장을 계기로 'AI 인프라에 쏟는 막대한 돈이 실제로 "
        "회수되나'라는 질문이 부각되며 반도체·AI주 중심 투매. 11/20 하루에만 AMD·마이크론 10% 안팎 급락.",
        "**반도체 고유 악재** — 메모리 가격(HBM·낸드) 고점 지속성 우려, 중국 메모리 업체 경쟁, "
        "TSMC·삼성의 일부 중국 판매 중단 보도. 필라델피아 반도체지수는 고점 대비 약 30% 하락 구간.",
        "**금리 인상 경계** — 물가가 다시 뜨거워지며 12월 FOMC에서 금리 인상 가능성이 거론. "
        "국채금리·달러 강세는 기술주 밸류에이션과 코인에 동시 부담.",
        "**코인 동반 급락** — 비트코인 6만 달러 붕괴(올해 최저), 이더리움 1,600달러대. "
        "11월 현물 ETF 대규모 순유출 + 레버리지 강제청산 연쇄 + 기술주 급락 전이.",
    ],
    "ahead": [
        "11/25 PCE 물가 → 12/4 고용보고서 → **12/9 FOMC(금리 결정)** → 12/10 CPI 순서로 "
        "'금리 인상 여부'가 판가름 — 연말까지 최대 변수",
        "반도체 실적: 12/10 브로드컴 → 12/18 마이크론 (+ 같은 날 쿼드러플 위칭) — "
        "AI 수요가 확인되면 반도체 반등의 계기, 실망 시 SOXL 추가 변동성",
        "과거 통계상 VIX 35 이상 공포 구간 이후 6개월 수익률은 대체로 양호했지만, "
        "그 과정의 단기 변동성(추가 하락 포함)은 매우 컸음 → 아래 '과거 유사 상황' 표 참고",
    ],
    "sources": [
        ("Motley Fool — Nov 20 반도체 급락", "https://www.fool.com/coverage/stock-market-today/2026/11/20/stock-market-midday-nov-20-nasdaq-plunges-nearly-3-as-amd-and-micron-lead-a-brutal-semiconductor-sell-off/"),
        ("Schwab — Tech Carnage Continues", "https://www.schwab.com/learn/story/stock-market-update-open"),
        ("Forbes — Bitcoin 2026 저점", "https://www.forbes.com/sites/antoniopequenoiv/2026/11/20/bitcoin-tanks-to-lowest-point-of-2026-amid-broader-cryptocurrency-sell-off/"),
        ("연준 FOMC 일정 (2027 개정)", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
        ("12월 경제지표 일정", "https://fedratecalc.com/us-economic-calendar/december-2026/"),
    ],
}
