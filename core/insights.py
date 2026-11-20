"""내 자산 인사이트 — RAW 간 변화 분석 · 방향성 진단 · 시나리오 스트레스 테스트 · 브리핑.

- compare_raws(): 직전 RAW 대비 '무엇이 왜 바뀌었나' (가격 효과 vs 매매·입출금 효과)
- stress_test(): 나스닥/반도체/코인/환율 충격 시 내 자산 변화 추정
- direction_checks(): 신호등(🔴🟡🟢) 점검 카드 + 종합 방향성
- briefing(): 홈 화면 '오늘의 브리핑' 문장

모든 결과는 판단 보조용 정보이며 매수·매도 권유가 아님.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

# ─────────────────────────────────────────────────────────────
#  1) RAW 간 변화 분석
# ─────────────────────────────────────────────────────────────


def _positions(raw: pd.DataFrame):
    from .raw_loader import split_holdings_and_cash
    h, c = split_holdings_and_cash(raw)
    if h.empty:
        pos = pd.DataFrame(columns=["ticker", "name", "qty", "eval", "cost"])
    else:
        h = h.copy()
        h["eval_krw"] = h["eval_krw"].fillna(0.0)
        h["cost_krw"] = h["cost_krw"].fillna(0.0)
        pos = (h.groupby("ticker", as_index=False)
                .agg(name=("item", "first"), qty=("qty", "sum"),
                     eval=("eval_krw", "sum"), cost=("cost_krw", "sum")))
    acc = pd.Series(dtype=float)
    if not h.empty:
        acc = h.groupby("account_alias")["eval_krw"].sum()
    cash_acc = c.groupby("account_alias")["eval_krw"].sum(min_count=1).fillna(0.0) \
        if not c.empty else pd.Series(dtype=float)
    acc_total = acc.add(cash_acc, fill_value=0.0)
    cash_total = float(c["eval_krw"].fillna(0.0).sum()) if not c.empty else 0.0
    return pos, acc_total, cash_total


def compare_raws(prev_raw: pd.DataFrame, cur_raw: pd.DataFrame,
                 prev_name: str = "", cur_name: str = "") -> dict:
    """직전 RAW → 최신 RAW 변화. JSON 직렬화 가능한 dict."""
    from .raw_loader import raw_timestamp
    p_pos, p_acc, p_cash = _positions(prev_raw)
    c_pos, c_acc, c_cash = _positions(cur_raw)

    p_ts, c_ts = raw_timestamp(prev_raw), raw_timestamp(cur_raw)
    m = pd.merge(p_pos, c_pos, on="ticker", how="outer", suffixes=("_p", "_c"))
    rows = []
    for _, r in m.iterrows():
        pq, cq = float(r.get("qty_p") or 0), float(r.get("qty_c") or 0)
        pe, ce = float(r.get("eval_p") or 0), float(r.get("eval_c") or 0)
        pq = 0.0 if pd.isna(pq) else pq
        cq = 0.0 if pd.isna(cq) else cq
        pe = 0.0 if pd.isna(pe) else pe
        ce = 0.0 if pd.isna(ce) else ce
        name = r.get("name_c") if isinstance(r.get("name_c"), str) and r.get("name_c") else r.get("name_p")
        if pq > 0 and cq > 0:
            pp, cp = pe / pq, ce / cq
            price_eff = pq * (cp - pp)
            qty_eff = (cq - pq) * cp
            status = "유지" if abs(cq - pq) / pq < 0.005 else ("추가매수" if cq > pq else "일부매도")
            price_chg = cp / pp - 1 if pp else 0.0
        elif cq > 0:
            price_eff, qty_eff, status, price_chg = 0.0, ce, "신규", None
        else:
            price_eff, qty_eff, status, price_chg = 0.0, -pe, "전량매도", None
        rows.append({"ticker": r["ticker"], "name": str(name or r["ticker"]),
                     "prev_qty": pq, "cur_qty": cq, "prev_eval": pe, "cur_eval": ce,
                     "price_chg": price_chg, "price_effect": price_eff,
                     "qty_effect": qty_eff, "status": status})
    rows.sort(key=lambda x: x["price_effect"])

    accounts = []
    for a in sorted(set(p_acc.index) | set(c_acc.index)):
        pv = float(p_acc.get(a, 0.0)) if a in p_acc.index else None
        cv = float(c_acc.get(a, 0.0)) if a in c_acc.index else None
        status = "신규 계좌" if pv is None else ("RAW에서 빠짐" if cv is None else "유지")
        accounts.append({"account": a or "기타", "prev": pv, "cur": cv,
                         "delta": (cv or 0.0) - (pv or 0.0), "status": status})

    prev_total = float(p_pos["eval"].sum()) + p_cash if not p_pos.empty else p_cash
    cur_total = float(c_pos["eval"].sum()) + c_cash if not c_pos.empty else c_cash
    market = sum(x["price_effect"] for x in rows)
    flow = (cur_total - prev_total) - market
    days = (c_ts - p_ts).days if (p_ts and c_ts) else None
    return {
        "prev_name": prev_name, "cur_name": cur_name,
        "prev_ts": p_ts.isoformat() if p_ts else None,
        "cur_ts": c_ts.isoformat() if c_ts else None,
        "days": days, "prev_total": prev_total, "cur_total": cur_total,
        "delta": cur_total - prev_total, "market_effect": market, "flow_effect": flow,
        "prev_cash": p_cash, "cur_cash": c_cash,
        "tickers": rows, "accounts": accounts,
    }


def latest_raw_diff() -> dict | None:
    """RAW 폴더의 최신 2개 파일 비교 (로컬 전용)."""
    from .raw_loader import all_raw_paths, load_raw
    paths = all_raw_paths()
    if len(paths) < 2:
        return None
    try:
        return compare_raws(load_raw(paths[-2]), load_raw(paths[-1]),
                            paths[-2].name, paths[-1].name)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
#  2) 노출 · 스트레스 테스트
# ─────────────────────────────────────────────────────────────
# 티커 → (팩터, 민감도). 팩터: NDX(나스닥), SEMI(반도체), CRYPTO(코인)
FACTOR_MAP = {
    "SOXL": ("SEMI", 3.0), "SOXX": ("SEMI", 1.0), "SMH": ("SEMI", 1.0), "SOXS": ("SEMI", -3.0),
    "NVDA": ("SEMI", 1.3), "AMD": ("SEMI", 1.4), "AVGO": ("SEMI", 1.2), "MU": ("SEMI", 1.4),
    "TSM": ("SEMI", 1.1),
    "QQQ": ("NDX", 1.0), "QLD": ("NDX", 2.0), "TQQQ": ("NDX", 3.0), "SQQQ": ("NDX", -3.0),
    "MAGS": ("NDX", 1.15), "IGV": ("NDX", 1.1), "TSLA": ("NDX", 1.8), "MSFT": ("NDX", 0.9),
    "GOOGL": ("NDX", 1.0), "GOOG": ("NDX", 1.0), "ADBE": ("NDX", 1.0), "ORCL": ("NDX", 1.3),
    "PLTR": ("NDX", 2.0), "AAPL": ("NDX", 1.0), "AMZN": ("NDX", 1.2), "META": ("NDX", 1.3),
    "ETH": ("CRYPTO", 1.25), "BTC": ("CRYPTO", 1.0), "USDT": ("CASH", 0.0),
}
FACTOR_KO = {"NDX": "나스닥", "SEMI": "반도체", "CRYPTO": "코인"}

SCENARIOS = [
    ("나스닥 -10%", {"NDX": -0.10, "SEMI": -0.13}),
    ("반도체 -15%", {"SEMI": -0.15}),
    ("코인 -20%", {"CRYPTO": -0.20}),
    ("환율 -5% (원화강세)", {"FX": -0.05}),
    ("박스권", {"NDX": -0.02, "SEMI": -0.04, "CRYPTO": -0.05}),
    ("복합 하락", {"NDX": -0.10, "SEMI": -0.15, "CRYPTO": -0.20, "FX": -0.03}),
    ("반등", {"NDX": 0.10, "SEMI": 0.15, "CRYPTO": 0.20}),
]
SCENARIO_DESC = {
    "박스권": "나스닥 -2%·반도체 -4%·코인 -5% (높은 변동성 속 소폭 하락 + 레버리지 끌림 가정)",
    "복합 하락": "나스닥 -10%·반도체 -15%·코인 -20%·환율 -3% 동시",
    "반등": "나스닥 +10%·반도체 +15%·코인 +20%",
}


def _factor_of(ticker: str, cls: str, lev: float) -> tuple[str, float]:
    t = (ticker or "").upper()
    if t in FACTOR_MAP:
        return FACTOR_MAP[t]
    if cls == "암호화폐":
        return ("CRYPTO", 1.0)
    return ("NDX", max(1.0, float(lev or 1.0)))


def exposures(invest_rows: pd.DataFrame, net_assets: float) -> dict:
    """팩터별 실효 노출 (원) 과 순자산 대비 배수."""
    out = {"NDX": 0.0, "SEMI": 0.0, "CRYPTO": 0.0}
    if invest_rows is None or invest_rows.empty:
        return {"exp": out, "ratio": {k: 0.0 for k in out}}
    for _, r in invest_rows.iterrows():
        f, b = _factor_of(r.get("티커", ""), r.get("분류", ""), r.get("레버리지", 1.0))
        if f in out:
            out[f] += float(r.get("평가금액", 0) or 0) * b
    ratio = {k: (v / net_assets if net_assets else 0.0) for k, v in out.items()}
    return {"exp": out, "ratio": ratio}


def stress_test(invest_rows: pd.DataFrame, net_assets: float) -> dict:
    """{시나리오: {delta, pct, desc, detail:[{name, delta}]}}"""
    res = {}
    if invest_rows is None or invest_rows.empty:
        return res
    for name, shocks in SCENARIOS:
        total = 0.0
        detail = []
        for _, r in invest_rows.iterrows():
            ev = float(r.get("평가금액", 0) or 0)
            if ev <= 0:
                continue
            f, b = _factor_of(r.get("티커", ""), r.get("분류", ""), r.get("레버리지", 1.0))
            mv = max(-0.95, b * shocks.get(f, 0.0))
            new = ev * (1 + mv)
            if str(r.get("통화", "")).upper() == "USD":
                new *= (1 + shocks.get("FX", 0.0))
            d = new - ev
            total += d
            detail.append({"name": r.get("종목명", r.get("티커", "")), "ticker": r.get("티커", ""),
                           "delta": d})
        detail.sort(key=lambda x: x["delta"])
        res[name] = {"delta": total, "pct": total / net_assets if net_assets else 0.0,
                     "desc": SCENARIO_DESC.get(name, name), "detail": detail}
    return res


# ─────────────────────────────────────────────────────────────
#  3) 방향성 진단 (신호등 카드)
# ─────────────────────────────────────────────────────────────
def _card(level: str, title: str, body: str, metric: str = "", area: str = "") -> dict:
    return {"level": level, "title": title, "body": body, "metric": metric, "area": area}


def direction_checks(*, s: dict, invest_rows: pd.DataFrame, all_rows: pd.DataFrame,
                     total_hist: pd.DataFrame | None, vix: float | None,
                     fg: dict | None, sig: dict | None, events: list[dict],
                     rebal: pd.DataFrame | None, stress: dict) -> dict:
    """신호등 점검 카드 목록 + 종합 방향성(stance)."""
    from .outlook import leverage_decay
    sig = sig or {}
    cards: list[dict] = []
    net = float(s.get("total_eval", 0) or 0)
    inv_total = float(invest_rows["평가금액"].sum()) if invest_rows is not None and not invest_rows.empty else 0.0

    # ① 시장 위험도
    if vix is not None:
        if vix >= 30:
            cards.append(_card("red", f"시장 공포 구간 — VIX {vix:.1f}",
                               "변동성이 평소(15~20)의 약 2배. 하루 등락폭이 커서 레버리지 상품은 "
                               "손익이 크게 출렁입니다. 급한 결정보다 계획·분할 접근이 유리한 구간.",
                               f"VIX {vix:.1f}", "시장"))
        elif vix >= 20:
            cards.append(_card("yellow", f"변동성 확대 — VIX {vix:.1f}",
                               "평소보다 변동성이 큰 구간. 레버리지 비중의 체감 위험이 커집니다.",
                               f"VIX {vix:.1f}", "시장"))
        else:
            cards.append(_card("green", f"시장 안정 — VIX {vix:.1f}", "변동성 낮은 구간.",
                               f"VIX {vix:.1f}", "시장"))

    # ② 레버리지 + 변동성 끌림
    lev_rows = invest_rows[invest_rows["레버리지"].astype(float) > 1] \
        if invest_rows is not None and not invest_rows.empty and "레버리지" in invest_rows else pd.DataFrame()
    lev_eval = float(lev_rows["평가금액"].sum()) if not lev_rows.empty else 0.0
    lev_share = lev_eval / inv_total if inv_total else 0.0
    if lev_eval > 0:
        decay_txt = []
        for _, r in lev_rows.groupby("티커", as_index=False).agg(
                평가금액=("평가금액", "sum"), 레버리지=("레버리지", "max")).iterrows():
            tk = str(r["티커"]).upper()
            sigma = sig.get("vol_soxx") if FACTOR_MAP.get(tk, ("", 0))[0] == "SEMI" else sig.get("vol_qqq")
            d = leverage_decay(float(r["레버리지"]), sigma)
            if d is not None:
                decay_txt.append(f"{tk} 월 {d*100:.1f}%")
        hi_vol = (vix or 0) >= 25
        lvl = "red" if (hi_vol and lev_share >= 0.25) else ("yellow" if lev_share >= 0.15 else "green")
        body = (f"레버리지 ETF가 투자자산의 {lev_share*100:.0f}% ({lev_eval/1e8:.2f}억). "
                "레버리지 ETF는 매일 배수를 맞추기 때문에, 오르내림이 반복되면 지수가 제자리여도 "
                "가치가 깎입니다(변동성 끌림).")
        if decay_txt:
            body += " 현재 변동성 기준 이론 손실: " + ", ".join(decay_txt) + " (지수 횡보 가정)."
        cards.append(_card(lvl, "레버리지 × 변동성 점검", body, f"{lev_share*100:.0f}%", "포트폴리오"))

    # ③ 실효 노출
    ex = exposures(invest_rows, net)
    eff = sum(ex["exp"].values())
    eff_ratio = eff / net if net else 0.0
    nd10 = stress.get("나스닥 -10%", {}).get("delta", 0.0)
    lvl = "red" if eff_ratio >= 1.6 else ("yellow" if eff_ratio >= 1.2 else "green")
    cards.append(_card(lvl, f"순자산 대비 시장 노출 {eff_ratio:.2f}배",
                       f"레버리지를 포함한 실효 노출 {eff/1e8:.2f}억 (나스닥 {ex['ratio']['NDX']:.2f}배 · "
                       f"반도체 {ex['ratio']['SEMI']:.2f}배 · 코인 {ex['ratio']['CRYPTO']:.2f}배). "
                       f"나스닥 10% 하락 시 약 {nd10/1e8:+.2f}억 ({nd10/net*100 if net else 0:+.1f}%) 추정.",
                       f"{eff_ratio:.2f}×", "포트폴리오"))

    # ④ 현금 여력
    cash = float(s.get("cash_eval", 0) or 0)
    if not cash and all_rows is not None and not all_rows.empty:
        cash = float(all_rows[all_rows["분류"] == "현금"]["평가금액"].clip(lower=0).sum())
    gross = float(s.get("gross_assets", 0) or net or 1)
    cs = cash / gross if gross else 0.0
    lvl = "red" if cs < 0.02 else ("yellow" if cs < 0.05 else "green")
    cards.append(_card(lvl, f"현금 비중 {cs*100:.1f}%",
                       f"투자 계좌 내 현금 {cash/1e4:,.0f}만원. "
                       + ("변동성 장에서 추가 대응(분할매수·비상자금) 여력이 거의 없는 상태입니다."
                          if cs < 0.02 else "급락 시 대응할 여유 현금이 일부 있습니다." if cs < 0.05
                          else "대응 여력이 충분합니다."),
                       f"{cs*100:.1f}%", "포트폴리오"))

    # ⑤ 테마 집중도
    if inv_total and "자산버킷" in invest_rows.columns:
        b = invest_rows.groupby("자산버킷")["평가금액"].sum().sort_values(ascending=False)
        tech = float(b[[i for i in b.index if any(k in str(i) for k in ("빅테크", "반도체", "AI"))]].sum())
        ts = tech / inv_total
        lvl = "red" if ts >= 0.85 else ("yellow" if ts >= 0.65 else "green")
        cards.append(_card(lvl, f"기술주 테마 집중 {ts*100:.0f}%",
                           "빅테크·AI/반도체 버킷 합계. 같은 뉴스(AI·금리)에 함께 움직여 분산 효과가 작습니다. "
                           f"가장 큰 버킷: {b.index[0]} {b.iloc[0]/inv_total*100:.0f}%.",
                           f"{ts*100:.0f}%", "포트폴리오"))

    # ⑥ 코인 단일 종목 집중
    coins = invest_rows[invest_rows["분류"] == "암호화폐"] if inv_total else pd.DataFrame()
    if not coins.empty:
        c_tot = float(coins["평가금액"].sum())
        top = coins.sort_values("평가금액", ascending=False).iloc[0]
        top_share = float(top["평가금액"]) / c_tot if c_tot else 0
        c_share = c_tot / inv_total if inv_total else 0
        lvl = "yellow" if (top_share > 0.9 and c_share > 0.12) else "green"
        cards.append(_card(lvl, f"코인 비중 {c_share*100:.0f}% (그중 {top['종목명']} {top_share*100:.0f}%)",
                           "코인은 주식보다 변동성이 2~3배 큽니다. 한 종목 집중이면 그 종목 이슈(ETF 유출 등)에 "
                           "직접 노출됩니다.", f"{c_share*100:.0f}%", "포트폴리오"))

    # ⑦ 원금 회복 거리 / 최고점 대비
    cost = float(s.get("total_cost", 0) or 0)
    inv_eval = inv_total
    if cost > 0 and inv_eval > 0:
        ret = inv_eval / cost - 1
        need = cost / inv_eval - 1
        lvl = "red" if ret <= -0.15 else ("yellow" if ret < 0 else "green")
        cards.append(_card(lvl, f"투자 수익률 {ret*100:+.1f}%",
                           (f"매수원금 회복까지 +{need*100:.0f}% 상승 필요 "
                            f"(손실 −{(cost-inv_eval)/1e8:.2f}억). 손실 폭이 클수록 회복에 필요한 상승률은 "
                            "더 커집니다 (−25% → +33%, −40% → +67%).") if ret < 0
                           else f"매수원금 대비 +{(inv_eval-cost)/1e8:.2f}억 수익 중.",
                           f"{ret*100:+.1f}%", "성과"))
    if total_hist is not None and len(total_hist) >= 2:
        th = total_hist.sort_values("date")
        peak = float(th["total"].max())
        cur = float(th["total"].iloc[-1])
        dd = cur / peak - 1 if peak else 0
        if dd < -0.03:
            lvl = "red" if dd <= -0.20 else "yellow"
            pk_d = pd.to_datetime(th.loc[th["total"].idxmax(), "date"])
            cards.append(_card(lvl, f"운용 총자산 최고점 대비 {dd*100:.1f}%",
                               f"최고 {peak/1e8:.2f}억({pk_d:%y.%m.%d}) → 현재 {cur/1e8:.2f}억.",
                               f"{dd*100:.1f}%", "성과"))

    # ⑧ 목표비중 이탈
    if rebal is not None and not rebal.empty and "차이(%p)" in rebal.columns:
        off = rebal[rebal["차이(%p)"].abs() >= 0.05]
        if not off.empty:
            def _n(r):
                nm = str(r.get("종목명", "") or "")
                return r.get("티커", nm) if (not nm or nm.isascii() or len(nm) > 8) else nm
            names = ", ".join(f"{_n(r)} {r['차이(%p)']*100:+.0f}%p" for _, r in off.head(5).iterrows())
            cards.append(_card("yellow", f"목표비중 이탈 {len(off)}개 종목",
                               f"설정한 목표비중 대비 ±5%p 이상: {names}. 시장 급변으로 비중이 의도와 "
                               "달라졌는지 점검해 보세요.", f"{len(off)}개", "포트폴리오"))

    # ⑨ 환율
    usd = float(all_rows[all_rows["통화"].astype(str).str.upper() == "USD"]["평가금액"].sum()) \
        if all_rows is not None and not all_rows.empty and "통화" in all_rows else 0.0
    if usd > 0 and gross:
        fx_now, fx_avg = sig.get("usdkrw"), sig.get("usdkrw_avg")
        extra = ""
        if fx_now and fx_avg:
            extra = f" 현재 환율 {fx_now:,.0f}원 (1년 평균 {fx_avg:,.0f}원 대비 {(fx_now/fx_avg-1)*100:+.1f}%)."
        cards.append(_card("gray", f"달러 자산 {usd/gross*100:.0f}%",
                           f"원화 강세(환율 하락) 시 원화 평가액 감소 — 환율 5% 하락 시 약 "
                           f"{-usd*0.05/1e4:,.0f}만원.{extra}", f"{usd/gross*100:.0f}%", "환율"))

    # ⑩ 2주 내 핵심 이벤트
    soon = [e for e in events if e.get("d_day", 99) <= 14 and e.get("imp", 0) >= 3 and e.get("mine")]
    if soon:
        txt = " · ".join(f"{e['date'].month}/{e['date'].day} {e['title'].split('(')[0].strip()}" for e in soon[:4])
        cards.append(_card("yellow", f"2주 내 핵심 이벤트 {len(soon)}개", txt + " — 발표 전후 변동성 확대 가능.",
                           f"{len(soon)}개", "일정"))

    # ⑪ 연말 세금 체크 (11~12월)
    if date.today().month in (11, 12):
        cards.append(_card("gray", "연말 해외주식 양도세 체크",
                           "해외주식 양도소득은 1~12월 실현 손익을 통산(기본공제 250만원, 초과분 22%). "
                           "올해 실현한 이익·손실 합계를 연말 전에 확인해 두세요.", "", "세금"))

    order = {"red": 0, "yellow": 1, "gray": 2, "green": 3}
    cards.sort(key=lambda c: order.get(c["level"], 9))
    reds = sum(c["level"] == "red" for c in cards)
    yellows = sum(c["level"] == "yellow" for c in cards)
    if reds >= 3 or (vix and vix >= 30 and reds >= 2):
        stance = ("🛡️ 방어 점검 우선", "시장 위험과 포트폴리오 위험(레버리지·집중·현금)이 겹친 상태. "
                  "수익 극대화보다 '버틸 수 있는 구조인지' 점검이 먼저인 구간입니다.")
    elif reds >= 1 or yellows >= 3:
        stance = ("⚖️ 균형 점검", "일부 위험 신호. 비중·현금·레버리지를 계획과 비교해 보세요.")
    else:
        stance = ("🚀 공격 여력 있음", "위험 신호가 적은 상태. 계획대로 운용 가능한 구간.")
    return {"cards": cards, "stance": stance, "reds": reds, "yellows": yellows,
            "exposure": ex, "eff_ratio": eff_ratio, "lev_share": lev_share, "cash_share": cs}


# ─────────────────────────────────────────────────────────────
#  4) 홈 브리핑
# ─────────────────────────────────────────────────────────────
def _e(t) -> str:
    import html
    return html.escape(str(t), quote=True).replace("$", "&#36;").replace("~", "&#126;")


def _nm(t: dict) -> str:
    n = str(t.get("name") or "")
    return _e(n if (n and len(n) <= 8 and not n.isascii()) else (t.get("ticker") or n[:10]))


def _short(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else "+"
    if a >= 1e8:
        return f"{sign}{a/1e8:.2f}억"
    return f"{sign}{a/1e4:,.0f}만"


def briefing(*, diff: dict | None, market: dict, vix: float | None, fg: dict | None,
             events: list[dict], diag: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if diff:
        try:
            pd_ = datetime.fromisoformat(diff["prev_ts"]) if diff.get("prev_ts") else None
            cd_ = datetime.fromisoformat(diff["cur_ts"]) if diff.get("cur_ts") else None
        except Exception:
            pd_ = cd_ = None
        span = f"{pd_:%m/%d} → {cd_:%m/%d}" if pd_ and cd_ else "직전 RAW 대비"
        pct = diff["delta"] / diff["prev_total"] * 100 if diff.get("prev_total") else 0
        out.append(("📄", f"<b>RAW {span}</b> 내 투자계좌 <b>{_short(diff['delta'])}</b> ({pct:+.1f}%) — "
                          f"시장 변동 {_short(diff['market_effect'])} · 매매·입출금 {_short(diff['flow_effect'])}"))
        movers = [t for t in diff.get("tickers", []) if t["price_effect"] != 0]
        neg = sorted(movers, key=lambda t: t["price_effect"])[:2]
        pos = sorted(movers, key=lambda t: t["price_effect"], reverse=True)[:1]
        bits = [f"{_nm(t)} <b>{_short(t['price_effect'])}</b>"
                for t in neg if t["price_effect"] < 0]
        bits += [f"{_nm(t)} <b>{_short(t['price_effect'])}</b>"
                 for t in pos if t["price_effect"] > 0]
        if bits:
            out.append(("🎯", "가장 크게 움직인 종목 (가격효과): " + " · ".join(bits)))
    if market:
        parts = []
        for k, nm in [("qqq", "QQQ"), ("soxl", "SOXL"), ("btc", "BTC"), ("eth", "ETH")]:
            if k in market:
                parts.append(f"{nm} {market[k]['week_change']*100:+.1f}%")
        mood = ""
        if vix is not None:
            mood = f" · VIX {vix:.1f}" + (" 🔴공포" if vix >= 30 else " 🟡주의" if vix >= 20 else " 🟢안정")
        if fg and fg.get("score") is not None:
            mood += f" · 공포탐욕 {fg['score']:.0f}"
        if parts:
            out.append(("📉" if any("-" in p for p in parts[:2]) else "📈",
                        "최근 1주 " + " · ".join(parts) + mood))
    key = [e for e in events if e.get("imp", 0) >= 3][:3]
    if key:
        from .events import dday_label, fmt_event_date
        out.append(("📅", "다가오는 핵심 일정: " + " · ".join(
            f"<b>{fmt_event_date(e['date'])} {_e(e['title'].split('(')[0].split('—')[0].strip())}</b> "
            f"({dday_label(e['d_day'])})" for e in key)))
    reds = [c for c in diag.get("cards", []) if c["level"] == "red"]
    if reds:
        out.append(("🧭", f"<b>{_e(diag['stance'][0])}</b> — 우선 점검: "
                    + " · ".join(_e(c["title"]) for c in reds[:3])))
    elif diag.get("stance"):
        out.append(("🧭", f"<b>{_e(diag['stance'][0])}</b> — {_e(diag['stance'][1])}"))
    return out
