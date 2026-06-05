from __future__ import annotations

import pandas as pd

from .config import PORTFOLIO_XLSX


def _normalize_columns(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    rename = {k: v for k, v in mapping.items() if k in df.columns}
    return df.rename(columns=rename)


def load_coins() -> pd.DataFrame:
    """portfolio.xlsx의 coins 시트.

    반환 컬럼: market, name, qty, avg_price_krw
    """
    if not PORTFOLIO_XLSX.exists():
        return _empty_coins()
    try:
        df = pd.read_excel(PORTFOLIO_XLSX, sheet_name="coins", engine="openpyxl")
    except ValueError:
        return _empty_coins()

    df = _normalize_columns(df, {
        "심볼": "market", "마켓": "market",
        "한글명": "name", "종목명": "name",
        "수량": "qty",
        "평단가": "avg_price_krw", "평단가(KRW)": "avg_price_krw",
    })
    df = df.dropna(subset=["market"]).copy()
    df["qty"] = pd.to_numeric(df.get("qty"), errors="coerce").fillna(0.0)
    df["avg_price_krw"] = pd.to_numeric(df.get("avg_price_krw"), errors="coerce").fillna(0.0)
    if "name" not in df.columns:
        df["name"] = df["market"]
    df = df[df["qty"] > 0]
    return df[["market", "name", "qty", "avg_price_krw"]].reset_index(drop=True)


def load_family() -> pd.DataFrame:
    """portfolio.xlsx의 family 시트 (가족 자산 수기 입력).

    반환 컬럼: label, amount, memo
    - 항목 = 승환 보증금 / 다은 보증금 / 다은 투자자산 등 자유 추가 가능
    - 금액(KRW). 음수 입력 시 부채로 취급 (운용 총자산에서 차감)
    """
    if not PORTFOLIO_XLSX.exists():
        return _empty_family()
    try:
        df = pd.read_excel(PORTFOLIO_XLSX, sheet_name="family", engine="openpyxl")
    except ValueError:
        return _empty_family()

    df = _normalize_columns(df, {
        "항목": "label", "구분": "label", "이름": "label",
        "금액": "amount", "금액(원)": "amount",
        "메모": "memo", "비고": "memo",
    })
    if "label" not in df.columns:
        return _empty_family()
    df = df.dropna(subset=["label"]).copy()
    df["label"] = df["label"].astype(str).str.strip()
    df = df[df["label"] != ""]
    df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0)
    df["memo"] = df.get("memo", "").fillna("").astype(str)
    return df[["label", "amount", "memo"]].reset_index(drop=True)


def load_meta() -> pd.DataFrame:
    """portfolio.xlsx의 meta 시트 (종목 마스터).

    반환 컬럼: ticker, name_kr, leverage, bucket, memo
    - 시트 없거나 비어있으면 빈 DF 반환 (대시보드는 기본값으로 동작)
    - leverage 미입력 → 1.0 / bucket 미입력 → "미분류" 는 calc.py에서 처리
    """
    if not PORTFOLIO_XLSX.exists():
        return _empty_meta()
    try:
        df = pd.read_excel(PORTFOLIO_XLSX, sheet_name="meta", engine="openpyxl")
    except ValueError:
        return _empty_meta()

    df = _normalize_columns(df, {
        "티커": "ticker", "심볼": "ticker", "Ticker": "ticker",
        "한글명": "name_kr", "종목명": "name_kr",
        "레버리지": "leverage", "레버리지배수": "leverage",
        "자산버킷": "bucket", "성격": "bucket", "분류": "bucket",
        "목표비중": "target_weight", "목표비중(%)": "target_weight", "목표": "target_weight",
        "비고": "memo", "메모": "memo",
    })
    if "ticker" not in df.columns:
        return _empty_meta()
    df = df.dropna(subset=["ticker"]).copy()
    df["ticker"] = df["ticker"].astype(str).str.strip()
    df = df[df["ticker"] != ""]
    if df.empty:
        return _empty_meta()

    df["leverage"] = pd.to_numeric(df.get("leverage"), errors="coerce").fillna(1.0)
    df["bucket"] = df.get("bucket", "").fillna("").astype(str).str.strip()
    df["bucket"] = df["bucket"].where(df["bucket"] != "", "미분류")
    df["name_kr"] = df.get("name_kr", "").fillna("").astype(str)
    df["memo"] = df.get("memo", "").fillna("").astype(str)
    # 목표비중: 사용자가 %를 5(=5%) 또는 0.05로 입력해도 둘 다 인식
    tw = pd.to_numeric(df.get("target_weight"), errors="coerce")
    tw = tw.where(tw.isna() | (tw <= 1.0), tw / 100.0)  # 1 초과 값은 %로 간주 → /100
    df["target_weight"] = tw.fillna(0.0)
    return df[["ticker", "name_kr", "leverage", "bucket", "target_weight", "memo"]].reset_index(drop=True)


def load_cash() -> pd.DataFrame:
    """portfolio.xlsx의 cash 시트.

    반환 컬럼: label, amount, memo  (모든 금액은 KRW 기준, 음수 = 부채)
    구버전의 '통화' 컬럼이 있어도 무시한다 (모두 KRW로 통일).
    """
    if not PORTFOLIO_XLSX.exists():
        return _empty_cash()
    try:
        df = pd.read_excel(PORTFOLIO_XLSX, sheet_name="cash", engine="openpyxl")
    except ValueError:
        return _empty_cash()

    df = _normalize_columns(df, {
        "구분": "label",
        "금액": "amount",
        "메모": "memo",
    })
    df = df.dropna(subset=["label"]).copy()
    df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0)
    df["memo"] = df.get("memo", "").fillna("")
    df = df[df["amount"] != 0]
    return df[["label", "amount", "memo"]].reset_index(drop=True)


def _empty_coins() -> pd.DataFrame:
    return pd.DataFrame(columns=["market", "name", "qty", "avg_price_krw"])


def _empty_meta() -> pd.DataFrame:
    return pd.DataFrame(columns=["ticker", "name_kr", "leverage", "bucket",
                                  "target_weight", "memo"])


def _empty_family() -> pd.DataFrame:
    return pd.DataFrame(columns=["label", "amount", "memo"])


def _empty_cash() -> pd.DataFrame:
    return pd.DataFrame(columns=["label", "amount", "memo"])
