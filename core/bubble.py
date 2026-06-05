"""AI 버블 고점 판독기.

여러 시장·심리·기술 지표를 투명한 가중치로 종합하여 0~100의 '버블 위험도'를
산출한다. 각 구성요소는 근거·수치·논리를 함께 반환하여 사용자가 판단 근거를
직접 확인할 수 있게 한다. (투자 자문이 아닌 참고용 휴리스틱)

구성요소 (가중치):
  1. 이평 이격도 (0.20) — 주요 AI/반도체 종목이 200일선 위 얼마나 떨어져 있나
  2. 모멘텀 RSI  (0.15) — 과매수 정도
  3. VIX 안주    (0.15) — 변동성 지나치게 낮으면 '안주(complacency)' = 버블 후기
  4. F&G 탐욕    (0.15) — 시장 심리 과열
  5. YTD 과열    (0.15) — 연초 대비 포물선 상승 정도
  6. 뉴스 과열   (0.10) — AI 키워드 + 긍정 심리 쏠림
  7. 내 노출     (0.10) — 내 포트폴리오의 AI/반도체 집중 + 레버리지
"""
from __future__ import annotations

import streamlit as st

from .config import PRICE_CACHE_TTL
from .prices import get_stock_quote

# 버블 진단 대표 종목 (AI/반도체/빅테크)
BENCHMARK_TICKERS = ["QQQ", "SMH", "NVDA", "SOXX"]


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lerp_score(value: float, points: list[tuple[float, float]]) -> float:
    """구간 선형보간. points = [(입력, 점수), ...] 오름차순 입력."""
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= value <= x1:
            t = (value - x0) / (x1 - x0) if x1 != x0 else 0
            return y0 + t * (y1 - y0)
    return points[-1][1]


@st.cache_data(ttl=PRICE_CACHE_TTL, show_spinner=False)
def _benchmark_metrics() -> dict:
    """대표 종목들의 이평이격·RSI·YTD 평균."""
    devs, rsis, ytds = [], [], []
    details = {}
    for t in BENCHMARK_TICKERS:
        try:
            q = get_stock_quote(t)
        except Exception:
            continue
        if not q or not q.price:
            continue
        price = float(q.price)
        if q.ma200:
            devs.append((price - float(q.ma200)) / float(q.ma200))
        if q.rsi14:
            rsis.append(float(q.rsi14))
        if q.ytd_open and float(q.ytd_open) > 0:
            ytds.append((price - float(q.ytd_open)) / float(q.ytd_open))
        details[t] = {
            "price": price,
            "dev_ma200": (price - float(q.ma200)) / float(q.ma200) if q.ma200 else None,
            "rsi": float(q.rsi14) if q.rsi14 else None,
            "ytd": (price - float(q.ytd_open)) / float(q.ytd_open) if q.ytd_open else None,
        }
    return {
        "avg_dev_ma200": sum(devs) / len(devs) if devs else None,
        "avg_rsi": sum(rsis) / len(rsis) if rsis else None,
        "avg_ytd": sum(ytds) / len(ytds) if ytds else None,
        "details": details,
    }


def compute_bubble_index(fg: dict, news_items: list, vix: float | None,
                         portfolio_ai_share: float = 0.0,
                         portfolio_leverage: float = 1.0) -> dict:
    """AI 버블 위험도 0~100 + 구성요소별 근거."""
    bm = _benchmark_metrics()
    comps: list[dict] = []

    # 1. 이평 이격도 (200일선 위 정도)
    dev = bm.get("avg_dev_ma200")
    if dev is not None:
        sc = _lerp_score(dev * 100, [(-10, 10), (0, 30), (10, 50), (25, 75), (40, 92), (60, 100)])
        comps.append({
            "name": "이평 이격도",
            "weight": 0.20,
            "score": sc,
            "value": f"+{dev*100:.1f}%" if dev >= 0 else f"{dev*100:.1f}%",
            "reason": f"주요 AI/반도체 종목(QQQ·SMH·NVDA·SOXX)이 200일 이동평균선 대비 "
                      f"평균 {dev*100:+.1f}% 위에 있습니다. 역사적으로 +25%↑ 이격은 단기 과열, "
                      f"+40%↑는 고점 부근에서 자주 나타납니다.",
        })

    # 2. 모멘텀 RSI
    rsi = bm.get("avg_rsi")
    if rsi is not None:
        sc = _lerp_score(rsi, [(30, 12), (50, 38), (60, 55), (70, 78), (80, 92), (90, 100)])
        comps.append({
            "name": "모멘텀 RSI",
            "weight": 0.15,
            "score": sc,
            "value": f"{rsi:.0f}",
            "reason": f"대표 종목 평균 RSI(14일)가 {rsi:.0f}입니다. 70↑은 과매수, "
                      f"80↑은 단기 과열 정점 신호로 해석됩니다.",
        })

    # 3. VIX 안주 (낮을수록 버블 후기 complacency)
    if vix is not None:
        sc = _lerp_score(vix, [(10, 95), (13, 82), (15, 70), (18, 55), (22, 38), (30, 18), (45, 8)])
        comps.append({
            "name": "VIX 안주",
            "weight": 0.15,
            "score": sc,
            "value": f"{vix:.1f}",
            "reason": f"VIX가 {vix:.1f}입니다. 강세장에서 VIX가 15 미만으로 지나치게 낮으면 "
                      f"투자자 '안주(complacency)' 상태로, 버블 후기에 흔합니다. "
                      f"반대로 25↑는 공포 구간으로 버블보다는 조정/하락 국면입니다.",
        })

    # 4. F&G 탐욕
    if fg and fg.get("score") is not None:
        fgs = float(fg["score"])
        sc = _lerp_score(fgs, [(10, 12), (25, 22), (45, 40), (55, 52), (70, 75), (80, 88), (95, 100)])
        comps.append({
            "name": "F&G 탐욕",
            "weight": 0.15,
            "score": sc,
            "value": f"{fgs:.0f}",
            "reason": f"CNN Fear & Greed가 {fgs:.0f}입니다. 75↑ '극단적 탐욕'은 시장 과열, "
                      f"버블 위험 누적 구간으로 평가됩니다.",
        })

    # 5. YTD 과열 (포물선 상승)
    ytd = bm.get("avg_ytd")
    if ytd is not None:
        sc = _lerp_score(ytd * 100, [(-20, 8), (0, 25), (20, 42), (50, 65), (100, 85), (200, 98)])
        comps.append({
            "name": "YTD 과열",
            "weight": 0.15,
            "score": sc,
            "value": f"+{ytd*100:.0f}%" if ytd >= 0 else f"{ytd*100:.0f}%",
            "reason": f"대표 종목 연초대비 평균 {ytd*100:+.0f}%입니다. 단기 +100%↑의 "
                      f"포물선(parabolic) 상승은 버블 후기 특징입니다.",
        })

    # 6. 뉴스 AI 과열 (AI 키워드 밀도 + 긍정 심리)
    if news_items:
        ai_kw = ["ai", "nvidia", "chip", "semiconductor", "openai", "데이터센터",
                 "gpu", "artificial intelligence"]
        ai_hits = 0
        for it in news_items:
            t = (it.get("title", "") + " " + it.get("summary", "")).lower()
            if any(k in t for k in ai_kw):
                ai_hits += 1
        ai_ratio = ai_hits / len(news_items)
        from .news import news_sentiment
        sent = news_sentiment(news_items)["score"]  # -1~1
        # AI 쏠림 + 긍정 심리 → 과열
        raw = ai_ratio * 70 + max(0, sent) * 30
        sc = _clamp(raw)
        comps.append({
            "name": "뉴스 AI 과열",
            "weight": 0.10,
            "score": sc,
            "value": f"AI {ai_hits}/{len(news_items)}건",
            "reason": f"주요 뉴스 {len(news_items)}건 중 {ai_hits}건이 AI/반도체 관련이며 "
                      f"헤드라인 심리는 {'긍정' if sent>0.1 else '부정' if sent<-0.1 else '중립'}입니다. "
                      f"AI 뉴스 쏠림 + 일방적 낙관은 과열 신호입니다.",
        })

    # 7. 내 포트폴리오 노출 (개인화)
    raw = portfolio_ai_share * 100 * 1.2 + max(0, (portfolio_leverage - 1)) * 40
    sc = _clamp(raw)
    comps.append({
        "name": "내 포트폴리오 노출",
        "weight": 0.10,
        "score": sc,
        "value": f"AI {portfolio_ai_share*100:.0f}% · {portfolio_leverage:.2f}×",
        "reason": f"내 투자자산의 AI/반도체 비중이 {portfolio_ai_share*100:.0f}%, "
                  f"가중 레버리지가 {portfolio_leverage:.2f}배입니다. 시장이 과열일 때 "
                  f"이 둘이 높으면 조정 시 손실 폭이 증폭됩니다.",
    })

    # 가중 종합
    total_w = sum(c["weight"] for c in comps)
    score = sum(c["score"] * c["weight"] for c in comps) / total_w if total_w else 0.0

    verdict, color, advice = _verdict(score)

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "color": color,
        "advice": advice,
        "components": comps,
        "benchmark_details": bm.get("details", {}),
    }


def _verdict(score: float) -> tuple[str, str, str]:
    if score < 25:
        return ("🟢 정상 / 저평가 구간", "green",
                "버블 신호 거의 없음. 분할 매수·정상 운용 가능 구간.")
    if score < 45:
        return ("🟢 건강한 상승", "green",
                "추세는 강하나 과열은 제한적. 기존 비중 유지하며 추세 추종 가능.")
    if score < 60:
        return ("🟡 과열 조짐", "orange",
                "단기 과열 신호 누적. 신규 레버리지 진입은 신중, 분할 익절 고려.")
    if score < 75:
        return ("🟠 버블 경계", "orange",
                "여러 지표가 동시 과열. 레버리지 축소·현금 비중 확대·익절 비중 늘릴 시점.")
    return ("🔴 버블 고점 위험", "red",
            "역사적 고점 패턴과 유사. 공격적 신규 진입 자제, 헷지·현금화 적극 검토.")
