from __future__ import annotations

import pandas as pd

from .config import ASSET_CASH, ASSET_COIN, ASSET_STOCK
from .prices import get_coin_quote, get_stock_quote, get_usd_krw

KIND_ETF = "ETF"
KIND_EQUITY = "개별주"
KIND_COIN = "코인"
KIND_CASH = "현금"
KIND_DEBT = "부채"


def _safe(x) -> float:
    return 0.0 if x is None or pd.isna(x) else float(x)


def _classify_stock(quote_type: str | None) -> str:
    if not quote_type:
        return KIND_EQUITY
    return KIND_ETF if quote_type.upper() == "ETF" else KIND_EQUITY


def _meta_lookup(meta_df: pd.DataFrame | None) -> dict[str, dict]:
    """티커 → {leverage, bucket, name_kr, target_weight} 매핑."""
    if meta_df is None or meta_df.empty:
        return {}
    out: dict[str, dict] = {}
    for _, r in meta_df.iterrows():
        out[str(r["ticker"]).strip().upper()] = {
            "leverage": float(r.get("leverage", 1.0) or 1.0),
            "bucket": str(r.get("bucket") or "미분류"),
            "name_kr": str(r.get("name_kr") or ""),
            "target_weight": float(r.get("target_weight", 0.0) or 0.0),
        }
    return out


def _meta_for(meta_map: dict[str, dict], ticker: str, default_bucket: str = "미분류") -> tuple[float, str]:
    info = meta_map.get(str(ticker).strip().upper())
    if not info:
        return 1.0, default_bucket
    return info["leverage"], info["bucket"]


def rebalancing_signals(invest_rows: pd.DataFrame, meta_df: pd.DataFrame,
                        threshold: float = 0.05) -> pd.DataFrame:
    """목표비중 vs 현재 비중 → 매수/매도 권장 금액.

    invest_rows: 투자자산만 (분류=해외주식+암호화폐)
    meta_df: target_weight 컬럼 포함
    threshold: 이탈 임계 (0.05 = ±5%p)

    반환 컬럼: 종목명, 티커, 현재비중, 목표비중, 차이(%p), 필요액(KRW),
              상태(🟢유지/🔴매도/🟢매수)
    """
    cols = ["종목명", "티커", "현재비중", "목표비중", "차이(%p)", "필요액(KRW)", "상태"]
    if invest_rows.empty or meta_df is None or meta_df.empty:
        return pd.DataFrame(columns=cols)

    meta_map = _meta_lookup(meta_df)
    if not any(v["target_weight"] > 0 for v in meta_map.values()):
        return pd.DataFrame(columns=cols)

    df = invest_rows[invest_rows["평가금액"] > 0].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    total = float(df["평가금액"].sum())
    rows = []
    for _, r in df.iterrows():
        ticker = str(r["티커"]).strip().upper()
        info = meta_map.get(ticker, {})
        target = info.get("target_weight", 0.0)
        eval_krw = float(r["평가금액"])
        current_w = eval_krw / total if total > 0 else 0.0
        diff_pp = current_w - target
        target_krw = total * target
        need = target_krw - eval_krw  # 양수 = 매수, 음수 = 매도
        if target == 0:
            status = "⚪ 목표 미설정"
        elif abs(diff_pp) < threshold:
            status = "🟢 유지"
        elif diff_pp > 0:
            status = "🔴 매도"
        else:
            status = "🟢 매수"
        rows.append({
            "종목명": r["종목명"],
            "티커": ticker,
            "현재비중": current_w,
            "목표비중": target,
            "차이(%p)": diff_pp,
            "필요액(KRW)": need,
            "상태": status,
        })
    return pd.DataFrame(rows, columns=cols).sort_values("차이(%p)", key=lambda s: s.abs(),
                                                         ascending=False).reset_index(drop=True)


FAMILY_DEBT_KEYWORDS = ("대출", "부채", "빚", "마이너스", "loan", "debt")


def _is_family_debt(label: str, amount: float) -> bool:
    """가족 항목이 부채인지 판단 — 라벨에 대출/부채 키워드 포함 or 금액 음수."""
    lab = str(label).lower()
    if any(kw in lab for kw in FAMILY_DEBT_KEYWORDS):
        return True
    return amount < 0


def family_summary(family_df: pd.DataFrame, my_net_eval: float) -> dict:
    """가족 자산(보증금·투자자산 등) + 대출(부채) + 운용 총자산 계산.

    - '대출/부채/빚' 키워드가 든 항목, 또는 음수 금액 = 부채로 차감.
    - 사용자는 대출 금액을 양수로 입력해도 됨 (라벨로 자동 인식).

    반환:
        family_assets : 가족 자산(보증금+투자자산 등) 합계 (양수)
        family_debt   : 가족 대출(부채) 합계 (양수)
        family_total  : 순 가족 기여 = 자산 - 부채
        items         : [{label, amount(부채는 음수), type, memo}]
        total_managed : 운용 총자산 = 내 순자산 + 가족자산 - 대출
        my_share/family_share : 운용 총자산 내 비중 (양수 기준)
    """
    if family_df is None or family_df.empty:
        return {"family_assets": 0.0, "family_debt": 0.0, "family_total": 0.0,
                "items": [], "total_managed": float(my_net_eval),
                "my_share": 1.0 if my_net_eval else 0.0, "family_share": 0.0}

    items = []
    assets = 0.0
    debt = 0.0
    for _, r in family_df.iterrows():
        label = str(r.get("label", ""))
        raw = float(r.get("amount", 0) or 0)
        amt = abs(raw)
        if _is_family_debt(label, raw):
            debt += amt
            items.append({"label": label, "amount": -amt, "type": "debt",
                          "memo": str(r.get("memo", "") or "")})
        else:
            assets += amt
            items.append({"label": label, "amount": amt, "type": "asset",
                          "memo": str(r.get("memo", "") or "")})

    family_net = assets - debt
    managed = float(my_net_eval) + family_net
    return {
        "family_assets": assets,
        "family_debt": debt,
        "family_total": family_net,
        "items": items,
        "total_managed": managed,
        "my_share": (my_net_eval / managed) if managed else 0.0,
        "family_share": (family_net / managed) if managed else 0.0,
    }


def currency_exposure(all_rows: pd.DataFrame) -> dict:
    """통화별 노출액 (KRW 환산 평가금액).

    USD 종목 평가액과 KRW 종목 평가액 비중 분리.
    """
    if all_rows.empty:
        return {"usd_krw": 0.0, "krw": 0.0, "usd_share": 0.0, "krw_share": 0.0}
    df = all_rows[all_rows["평가금액"] > 0]
    if df.empty:
        return {"usd_krw": 0.0, "krw": 0.0, "usd_share": 0.0, "krw_share": 0.0}
    usd = float(df[df["통화"] == "USD"]["평가금액"].sum())
    krw = float(df[df["통화"] != "USD"]["평가금액"].sum())
    total = usd + krw
    return {
        "usd_krw": usd,
        "krw": krw,
        "usd_share": usd / total if total > 0 else 0.0,
        "krw_share": krw / total if total > 0 else 0.0,
    }


def build_rows_from_raw(holdings: pd.DataFrame, cash: pd.DataFrame,
                        meta_df: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """RAW 데이터 → (stock_rows, coin_rows, cash_rows).

    RAW의 평가금액/매수금액/수익률은 그대로 신뢰.
    일간/주간/월간/YTD 등락률은 yfinance·업비트로 보강.
    레버리지·자산버킷은 meta 시트에서 조인.
    """
    meta_map = _meta_lookup(meta_df)
    stock_rows: list[dict] = []
    coin_rows: list[dict] = []

    for _, r in holdings.iterrows():
        ticker = str(r.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        qty = _safe(r.get("qty"))
        avg_krw = _safe(r.get("avg_price_krw"))
        cur_krw = _safe(r.get("current_price_krw"))
        eval_krw = _safe(r.get("eval_krw"))
        cost_krw = _safe(r.get("cost_krw"))
        if cost_krw == 0 and qty > 0 and avg_krw > 0:
            cost_krw = qty * avg_krw
        if eval_krw == 0 and qty > 0 and cur_krw > 0:
            eval_krw = qty * cur_krw
        pnl = _safe(r.get("pnl_krw"))
        if pnl == 0 and (eval_krw or cost_krw):
            pnl = eval_krw - cost_krw
        ret_pct = _safe(r.get("return_pct"))
        ret = ret_pct / 100.0 if ret_pct else ((pnl / cost_krw) if cost_krw > 0 else 0.0)
        currency = (r.get("currency") or "KRW").strip().upper()
        account_type = (r.get("account_type") or "").strip()
        account = (r.get("account_alias") or "").strip() or account_type
        name = (r.get("item") or ticker).strip()

        is_coin = account_type == "코인" or currency != "USD" and account_type != "미국주식"

        # 다기간 등락률 + fundamentals: yfinance·업비트로 보강
        if is_coin:
            market_for_quote = ticker if ticker.startswith("KRW-") else f"KRW-{ticker}"
            day_chg, week_chg, month_chg, ytd_chg, fund = _coin_period_changes(market_for_quote, cur_krw)
        else:
            day_chg, week_chg, month_chg, ytd_chg, fund = _stock_period_changes(ticker, cur_krw, currency)

        leverage, bucket = _meta_for(meta_map, ticker,
                                     default_bucket="암호화폐" if is_coin else "미분류")

        row = {
            "분류": ASSET_COIN if is_coin else ASSET_STOCK,
            "종류": KIND_COIN if is_coin else _classify_from_ticker(ticker),
            "계좌": account,
            "종목명": name,
            "티커": ticker,
            "수량": qty,
            "평단가": avg_krw,
            "현재가": cur_krw,
            "통화": currency,
            "평가금액": eval_krw,
            "매수금액": cost_krw,
            "손익": pnl,
            "수익률": ret,
            "일간": day_chg,
            "주간": week_chg,
            "월간": month_chg,
            "YTD": ytd_chg,
            "레버리지": leverage,
            "자산버킷": bucket,
            "52주위치": fund.get("pos_52w", 0.0),
            "RSI": fund.get("rsi14", 0.0),
            "MA50대비": fund.get("vs_ma50", 0.0),
            "MA200대비": fund.get("vs_ma200", 0.0),
            "변동성": fund.get("vol_30d", 0.0),
        }
        if is_coin:
            coin_rows.append(row)
        else:
            stock_rows.append(row)

    cash_rows: list[dict] = []
    for _, r in cash.iterrows():
        amount = _safe(r.get("eval_krw"))
        if amount == 0:
            continue
        label = (r.get("account_alias") or "").strip()
        item = (r.get("item") or "").strip()
        full_label = f"{label} {item}".strip()
        cash_rows.append({
            "분류": ASSET_CASH,
            "종류": KIND_DEBT if amount < 0 else KIND_CASH,
            "계좌": label or "현금",
            "종목명": full_label or "현금",
            "티커": "",
            "수량": amount,
            "평단가": 1.0,
            "현재가": 1.0,
            "통화": "KRW",
            "평가금액": amount,
            "매수금액": amount,
            "손익": 0.0,
            "수익률": 0.0,
            "일간": 0.0,
            "주간": 0.0,
            "월간": 0.0,
            "YTD": 0.0,
            "레버리지": 0.0,
            "자산버킷": "현금/부채",
            "52주위치": 0.0,
            "RSI": 0.0,
            "MA50대비": 0.0,
            "MA200대비": 0.0,
            "변동성": 0.0,
        })

    return (
        pd.DataFrame(stock_rows) if stock_rows else _empty_rows(),
        pd.DataFrame(coin_rows) if coin_rows else _empty_rows(),
        pd.DataFrame(cash_rows) if cash_rows else _empty_rows(),
    )


def _classify_from_ticker(ticker: str) -> str:
    """티커 이름만으로 ETF/개별주 추정. 정확한 분류는 meta 또는 yfinance."""
    ETF_HINTS = {"SOXL", "QLD", "TQQQ", "QQQ", "SPY", "MAGS", "IGV", "SQQQ", "DIA", "VOO", "VTI"}
    return KIND_ETF if ticker.upper() in ETF_HINTS else KIND_EQUITY


def _stock_period_changes(ticker: str, current_local_price: float, currency: str
                          ) -> tuple[float, float, float, float, dict]:
    """yfinance 1년치 → (일간, 주간, 월간, YTD, fundamentals dict)."""
    try:
        q = get_stock_quote(ticker)
    except Exception:
        return 0.0, 0.0, 0.0, 0.0, {}
    price = float(q.price) if q.price else current_local_price
    changes = _period_changes(price, q.prev_close, q.week_ago_close,
                              q.month_ago_close, q.ytd_open)
    fund = _fundamentals(price, q.high_52w, q.low_52w, q.ma50, q.ma200,
                         q.rsi14, q.volatility_30d)
    return (*changes, fund)


def _coin_period_changes(market: str, current_price: float
                         ) -> tuple[float, float, float, float, dict]:
    """업비트 200일치 → (일간, 주간, 월간, YTD, fundamentals)."""
    try:
        q = get_coin_quote(market)
    except Exception:
        return 0.0, 0.0, 0.0, 0.0, {}
    price = float(q.price) if q.price else current_price
    changes = _period_changes(price, q.prev_close, q.week_ago_close,
                              q.month_ago_close, q.ytd_open)
    fund = _fundamentals(price, q.high_52w, q.low_52w, None, None,
                         q.rsi14, None)
    return (*changes, fund)


def _fundamentals(price, high_52w, low_52w, ma50, ma200, rsi, vol30) -> dict:
    """종목 진단 지표 dict."""
    def pct(num, base):
        try:
            num = float(num); base = float(base)
            return (num - base) / base if base else 0.0
        except (TypeError, ValueError):
            return 0.0

    def safe(x):
        try:
            return float(x) if x is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    pos_52w = 0.0
    h = safe(high_52w); l = safe(low_52w); p = safe(price)
    if h > l > 0:
        pos_52w = (p - l) / (h - l)  # 0 = 52주 최저, 1 = 52주 최고

    return {
        "high_52w": safe(high_52w),
        "low_52w": safe(low_52w),
        "pos_52w": pos_52w,
        "ma50": safe(ma50),
        "ma200": safe(ma200),
        "vs_ma50": pct(price, ma50),
        "vs_ma200": pct(price, ma200),
        "rsi14": safe(rsi),
        "vol_30d": safe(vol30),
    }


def _period_changes(price, prev, week, month, ytd) -> tuple[float, float, float, float]:
    def chg(p, base):
        try:
            base = float(base)
            p = float(p)
            return (p - base) / base if base > 0 else 0.0
        except (TypeError, ValueError):
            return 0.0
    return chg(price, prev), chg(price, week), chg(price, month), chg(price, ytd)


def build_stock_rows(shinhan_df: pd.DataFrame, usd_krw: float,
                     meta_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """신한 PDF 보유 → 평가금액/수익률 테이블 (계좌별 row 유지).

    신한 PDF의 '평균가'는 이미 KRW 환산값이므로 FX 곱하지 않음.
    '현재가'는 yfinance USD → 현재환율 곱해서 KRW로 변환.
    """
    if shinhan_df.empty:
        return _empty_rows()

    meta_map = _meta_lookup(meta_df)
    rows = []
    for _, r in shinhan_df.iterrows():
        ticker = str(r["ticker"]).strip()
        qty = _safe(r["qty"])
        avg_krw = _safe(r["avg_price_krw"])  # 이미 KRW
        currency = (r.get("currency") or "USD").upper()
        name = (r.get("name") or ticker).strip()
        account = r.get("account") or ""

        q = get_stock_quote(ticker)
        fx = usd_krw if currency == "USD" else 1.0
        price_local = _safe(q.price)
        prev_local = _safe(q.prev_close)
        week_local = _safe(q.week_ago_close)
        month_local = _safe(q.month_ago_close)
        ytd_local = _safe(q.ytd_open)

        price_krw = price_local * fx
        cost_krw = qty * avg_krw
        eval_krw = qty * price_krw
        pnl = eval_krw - cost_krw
        ret = (pnl / cost_krw) if cost_krw > 0 else 0.0
        day_chg = ((price_local - prev_local) / prev_local) if prev_local > 0 else 0.0
        week_chg = ((price_local - week_local) / week_local) if week_local > 0 else 0.0
        month_chg = ((price_local - month_local) / month_local) if month_local > 0 else 0.0
        ytd_chg = ((price_local - ytd_local) / ytd_local) if ytd_local > 0 else 0.0
        leverage, bucket = _meta_for(meta_map, ticker)

        rows.append({
            "분류": ASSET_STOCK,
            "종류": _classify_stock(q.quote_type),
            "계좌": account,
            "종목명": name,
            "티커": ticker,
            "수량": qty,
            "평단가": avg_krw,
            "현재가": price_krw,
            "통화": currency,
            "평가금액": eval_krw,
            "매수금액": cost_krw,
            "손익": pnl,
            "수익률": ret,
            "일간": day_chg,
            "주간": week_chg,
            "월간": month_chg,
            "YTD": ytd_chg,
            "레버리지": leverage,
            "자산버킷": bucket,
        })
    return pd.DataFrame(rows)


def build_coin_rows(coins_df: pd.DataFrame,
                    meta_df: pd.DataFrame | None = None) -> pd.DataFrame:
    if coins_df.empty:
        return _empty_rows()
    meta_map = _meta_lookup(meta_df)
    rows = []
    for _, r in coins_df.iterrows():
        market = str(r["market"]).strip()
        qty = _safe(r["qty"])
        avg = _safe(r["avg_price_krw"])
        name = (r.get("name") or market).strip()

        q = get_coin_quote(market)
        price = _safe(q.price)
        prev = _safe(q.prev_close)
        week = _safe(q.week_ago_close)
        month = _safe(q.month_ago_close)
        ytd = _safe(q.ytd_open)

        cost_krw = qty * avg
        eval_krw = qty * price
        pnl = eval_krw - cost_krw
        ret = (pnl / cost_krw) if cost_krw > 0 else 0.0
        day_chg = ((price - prev) / prev) if prev > 0 else 0.0
        week_chg = ((price - week) / week) if week > 0 else 0.0
        month_chg = ((price - month) / month) if month > 0 else 0.0
        ytd_chg = ((price - ytd) / ytd) if ytd > 0 else 0.0
        leverage, bucket = _meta_for(meta_map, market, default_bucket="암호화폐")

        rows.append({
            "분류": ASSET_COIN,
            "종류": KIND_COIN,
            "계좌": "업비트",
            "종목명": name,
            "티커": market,
            "수량": qty,
            "평단가": avg,
            "현재가": price,
            "통화": "KRW",
            "평가금액": eval_krw,
            "매수금액": cost_krw,
            "손익": pnl,
            "수익률": ret,
            "일간": day_chg,
            "주간": week_chg,
            "월간": month_chg,
            "YTD": ytd_chg,
            "레버리지": leverage,
            "자산버킷": bucket,
        })
    return pd.DataFrame(rows)


def build_cash_rows(cash_df: pd.DataFrame, usd_krw: float) -> pd.DataFrame:
    """예수금/대출. 모두 KRW 기준. 음수 금액 = 부채(대출)."""
    if cash_df.empty:
        return _empty_rows()
    rows = []
    for _, r in cash_df.iterrows():
        label = str(r["label"]).strip()
        amount = _safe(r["amount"])
        rows.append({
            "분류": ASSET_CASH,
            "종류": KIND_DEBT if amount < 0 else KIND_CASH,
            "계좌": label,
            "종목명": label,
            "티커": "",
            "수량": amount,
            "평단가": 1.0,
            "현재가": 1.0,
            "통화": "KRW",
            "평가금액": amount,
            "매수금액": amount,
            "손익": 0.0,
            "수익률": 0.0,
            "일간": 0.0,
            "주간": 0.0,
            "월간": 0.0,
            "YTD": 0.0,
            "레버리지": 0.0,
            "자산버킷": "현금/부채",
        })
    return pd.DataFrame(rows)


def combine(stock: pd.DataFrame, coin: pd.DataFrame, cash: pd.DataFrame) -> pd.DataFrame:
    frames = [df for df in (stock, coin, cash) if not df.empty]
    if not frames:
        return _empty_rows()
    return pd.concat(frames, ignore_index=True)


def aggregate_by_ticker(stock_rows: pd.DataFrame) -> pd.DataFrame:
    """같은 종목이 여러 계좌에 있을 때 가중평균 합산.

    수익률은 (총평가 - 총매수)/총매수로 재계산.
    일간/주간은 가격 기준이라 종목당 1개 값으로 통일.
    """
    if stock_rows.empty:
        return _empty_rows()

    rows = []
    for ticker, g in stock_rows.groupby("티커", sort=False):
        qty = float(g["수량"].sum())
        cost = float(g["매수금액"].sum())
        ev = float(g["평가금액"].sum())
        accounts = ", ".join(sorted(g["계좌"].unique()))
        rows.append({
            "분류": g["분류"].iloc[0],
            "종류": g["종류"].iloc[0] if "종류" in g.columns else KIND_EQUITY,
            "계좌": accounts,
            "종목명": g["종목명"].iloc[0],
            "티커": ticker,
            "수량": qty,
            "평단가": cost / qty if qty > 0 else 0.0,
            "현재가": g["현재가"].iloc[0],
            "통화": g["통화"].iloc[0],
            "평가금액": ev,
            "매수금액": cost,
            "손익": ev - cost,
            "수익률": (ev - cost) / cost if cost > 0 else 0.0,
            "일간": float(g["일간"].iloc[0]),
            "주간": float(g["주간"].iloc[0]),
            "월간": float(g["월간"].iloc[0]) if "월간" in g.columns else 0.0,
            "YTD": float(g["YTD"].iloc[0]) if "YTD" in g.columns else 0.0,
            "레버리지": float(g["레버리지"].iloc[0]) if "레버리지" in g.columns else 1.0,
            "자산버킷": g["자산버킷"].iloc[0] if "자산버킷" in g.columns else "미분류",
            "52주위치": float(g["52주위치"].iloc[0]) if "52주위치" in g.columns else 0.0,
            "RSI": float(g["RSI"].iloc[0]) if "RSI" in g.columns else 0.0,
            "MA50대비": float(g["MA50대비"].iloc[0]) if "MA50대비" in g.columns else 0.0,
            "MA200대비": float(g["MA200대비"].iloc[0]) if "MA200대비" in g.columns else 0.0,
            "변동성": float(g["변동성"].iloc[0]) if "변동성" in g.columns else 0.0,
        })
    return pd.DataFrame(rows)


def summary(all_rows: pd.DataFrame) -> dict:
    """KPI. 현금 음수(부채)도 평가금액에 그대로 반영 → 순자산."""
    if all_rows.empty:
        return {"total_eval": 0, "total_cost": 0, "total_pnl": 0,
                "total_return": 0, "day_change": 0, "week_change": 0,
                "debt": 0, "gross_assets": 0}

    investable = all_rows[all_rows["분류"] != ASSET_CASH]
    cash_rows = all_rows[all_rows["분류"] == ASSET_CASH]

    gross = float(investable["평가금액"].sum()) + float(cash_rows[cash_rows["평가금액"] > 0]["평가금액"].sum())
    debt = float(-cash_rows[cash_rows["평가금액"] < 0]["평가금액"].sum())  # 양수로 표시
    total_eval = float(all_rows["평가금액"].sum())  # 부채 차감된 순자산

    total_cost_inv = float(investable["매수금액"].sum())
    total_eval_inv = float(investable["평가금액"].sum())
    pnl = total_eval_inv - total_cost_inv
    ret = pnl / total_cost_inv if total_cost_inv > 0 else 0.0

    day_change = _weighted_change(investable, "일간")
    week_change = _weighted_change(investable, "주간")

    return {
        "total_eval": total_eval,
        "total_cost": total_cost_inv,
        "total_pnl": pnl,
        "total_return": ret,
        "day_change": day_change,
        "week_change": week_change,
        "debt": debt,
        "gross_assets": gross,
    }


def _weighted_change(df: pd.DataFrame, col: str) -> float:
    if df.empty:
        return 0.0
    weights = df["평가금액"].astype(float)
    total = float(weights.sum())
    if total <= 0:
        return 0.0
    return float((df[col].astype(float) * weights).sum() / total)


def allocation_by_class(all_rows: pd.DataFrame) -> pd.DataFrame:
    """자산군별 비중 (해외주식/암호화폐/현금). 부채 제외."""
    return _allocation(all_rows, "분류")


def allocation_by_kind(all_rows: pd.DataFrame) -> pd.DataFrame:
    """종류별 비중 (ETF/개별주/코인/현금). 부채 제외."""
    return _allocation(all_rows, "종류")


def allocation_invest_only(all_rows: pd.DataFrame) -> pd.DataFrame:
    """투자자산만 (주식 vs 코인). 현금·부채 제외."""
    if all_rows.empty:
        return pd.DataFrame(columns=["분류", "평가금액", "비중"])
    df = all_rows[all_rows["분류"].isin([ASSET_STOCK, ASSET_COIN])].copy()
    df = df[df["평가금액"] > 0]
    if df.empty:
        return pd.DataFrame(columns=["분류", "평가금액", "비중"])
    g = df.groupby("분류", as_index=False)["평가금액"].sum()
    total = float(g["평가금액"].sum())
    g["비중"] = g["평가금액"] / total if total > 0 else 0.0
    return g.sort_values("평가금액", ascending=False).reset_index(drop=True)


def _allocation(all_rows: pd.DataFrame, key: str) -> pd.DataFrame:
    if all_rows.empty or key not in all_rows.columns:
        return pd.DataFrame(columns=[key, "평가금액", "비중"])
    df = all_rows[all_rows["평가금액"] > 0].copy()
    if df.empty:
        return pd.DataFrame(columns=[key, "평가금액", "비중"])
    g = df.groupby(key, as_index=False)["평가금액"].sum()
    total = float(g["평가금액"].sum())
    g["비중"] = g["평가금액"] / total if total > 0 else 0.0
    return g.sort_values("평가금액", ascending=False).reset_index(drop=True)


def concentration(invest_rows: pd.DataFrame) -> dict:
    """포트폴리오 집중도 지표.

    - top1_share: 1위 종목 비중
    - top3_share: 상위 3종목 비중
    - hhi: 허핀달-허쉬만 지수 (0~1, 높을수록 집중) — 0.18 이상이면 매우 집중
    - n_holdings: 종목 수
    """
    if invest_rows.empty:
        return {"top1_share": 0, "top3_share": 0, "hhi": 0, "n_holdings": 0}
    df = invest_rows[invest_rows["평가금액"] > 0].copy()
    if df.empty:
        return {"top1_share": 0, "top3_share": 0, "hhi": 0, "n_holdings": 0}
    total = float(df["평가금액"].sum())
    shares = (df["평가금액"] / total).sort_values(ascending=False)
    return {
        "top1_share": float(shares.iloc[0]),
        "top3_share": float(shares.head(3).sum()),
        "hhi": float((shares ** 2).sum()),
        "n_holdings": int(len(df)),
    }


def weighted_leverage(invest_rows: pd.DataFrame) -> dict:
    """포트폴리오 가중평균 레버리지와 실효 시장 노출액.

    - weighted: Σ(평가금액 × 레버리지) / Σ평가금액 (현금/부채 제외)
    - effective_exposure: Σ(평가금액 × 레버리지) (실제 시장 노출 KRW)
    """
    if invest_rows.empty or "레버리지" not in invest_rows.columns:
        return {"weighted": 0.0, "effective_exposure": 0.0}
    df = invest_rows[invest_rows["평가금액"] > 0].copy()
    if df.empty:
        return {"weighted": 0.0, "effective_exposure": 0.0}
    eval_sum = float(df["평가금액"].sum())
    lev = df["레버리지"].astype(float).fillna(1.0)
    exposure = float((df["평가금액"].astype(float) * lev).sum())
    return {
        "weighted": exposure / eval_sum if eval_sum > 0 else 0.0,
        "effective_exposure": exposure,
    }


def leverage_ratio(s: dict) -> dict:
    """대출/자산 관련 비율.

    - ltv: 부채 / 총자산 (대출이 총자산 대비 몇 %)
    - gross_to_net: 총자산 / 순자산 (대출로 부풀린 정도)
    """
    gross = float(s.get("gross_assets", 0) or 0)
    debt = float(s.get("debt", 0) or 0)
    net = float(s.get("total_eval", 0) or 0)
    return {
        "ltv": (debt / gross) if gross > 0 else 0.0,
        "gross_to_net": (gross / net) if net > 0 else 1.0,
    }


def risk_metrics(snapshots_df: pd.DataFrame, risk_free_rate: float = 0.03) -> dict:
    """스냅샷 시계열 기반 포트폴리오 리스크 지표.

    - daily_vol_ann: 일별 수익률 stdev × √252 (연환산 변동성)
    - mdd: 최대 낙폭 (음수, 예: -0.18 = -18%)
    - sharpe: (연환산 수익률 - 무위험) / 연환산 변동성
    - sortino: (연환산 수익률 - 무위험) / 하방 편차
    - cagr: 연환산 누적 수익률
    - best_day / worst_day: 최고·최저 일간 수익률
    - win_rate: 양수일 비율

    스냅샷이 부족하면 0 또는 None.
    """
    out = {"daily_vol_ann": 0.0, "mdd": 0.0, "sharpe": 0.0, "sortino": 0.0,
           "cagr": 0.0, "best_day": 0.0, "worst_day": 0.0, "win_rate": 0.0,
           "n_days": 0}
    if snapshots_df is None or snapshots_df.empty or "total_eval" not in snapshots_df.columns:
        return out
    s = snapshots_df.sort_values("date").copy()
    s = s[s["total_eval"] > 0]
    if len(s) < 2:
        return out

    rets = s["total_eval"].pct_change().dropna()
    if rets.empty:
        return out

    n = len(rets)
    out["n_days"] = int(n)
    out["best_day"] = float(rets.max())
    out["worst_day"] = float(rets.min())
    out["win_rate"] = float((rets > 0).mean())

    std = float(rets.std())
    out["daily_vol_ann"] = std * (252 ** 0.5)

    # MDD: running max 대비 최저 낙폭
    series = s["total_eval"].astype(float)
    running_max = series.cummax()
    drawdown = (series - running_max) / running_max
    out["mdd"] = float(drawdown.min())

    # CAGR (스냅샷이 며칠밖에 없으면 의미 적음)
    days = (s["date"].iloc[-1] - s["date"].iloc[0]).days
    if days >= 1:
        total_growth = float(series.iloc[-1] / series.iloc[0])
        years = max(days / 365.25, 1 / 365.25)
        out["cagr"] = total_growth ** (1 / years) - 1

    # Sharpe / Sortino (연환산)
    if std > 0:
        excess = rets.mean() * 252 - risk_free_rate
        out["sharpe"] = float(excess / out["daily_vol_ann"])
        downside = rets[rets < 0].std()
        if downside and downside > 0:
            out["sortino"] = float(excess / (downside * (252 ** 0.5)))

    return out


def allocation_by_bucket(all_rows: pd.DataFrame) -> pd.DataFrame:
    """자산버킷별 비중. 현금/부채는 별도 버킷으로 묶임."""
    if all_rows.empty or "자산버킷" not in all_rows.columns:
        return pd.DataFrame(columns=["자산버킷", "평가금액", "비중"])
    df = all_rows[all_rows["평가금액"] > 0].copy()
    if df.empty:
        return pd.DataFrame(columns=["자산버킷", "평가금액", "비중"])
    g = df.groupby("자산버킷", as_index=False)["평가금액"].sum()
    total = float(g["평가금액"].sum())
    g["비중"] = g["평가금액"] / total if total > 0 else 0.0
    return g.sort_values("평가금액", ascending=False).reset_index(drop=True)


def pnl_contribution(invest_rows: pd.DataFrame) -> pd.DataFrame:
    """종목별 손익 기여도. 양수/음수 모두."""
    if invest_rows.empty:
        return pd.DataFrame(columns=["종목명", "손익"])
    df = invest_rows[["종목명", "손익"]].copy()
    return df.sort_values("손익", ascending=False).reset_index(drop=True)


def _empty_rows() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "분류", "종류", "계좌", "종목명", "티커", "수량", "평단가", "현재가", "통화",
        "평가금액", "매수금액", "손익", "수익률", "일간", "주간", "월간", "YTD",
        "레버리지", "자산버킷",
        "52주위치", "RSI", "MA50대비", "MA200대비", "변동성",
    ])


def load_fx_safe() -> tuple[float, str | None]:
    try:
        return get_usd_krw(), None
    except Exception as e:
        return 1.0, f"환율 조회 실패 ({e})"
