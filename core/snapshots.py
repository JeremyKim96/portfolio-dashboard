"""시계열 스냅샷.

매번 앱 실행 시 현재 보유 상태를 `data/history/snapshots.csv`에 한 줄씩 추가한다.
같은 날짜의 기록이 이미 있으면 최신값으로 덮어쓴다.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import HISTORY_DIR

SNAPSHOT_FILE = HISTORY_DIR / "snapshots.csv"

_COLS = [
    "date",
    "ts",
    "total_eval",    # 순자산 (부채 차감)
    "gross_assets",  # 총자산
    "debt",          # 부채
    "total_cost",    # 투자 매수금액
    "total_pnl",     # 평가손익
    "total_return",  # 누적 수익률
    "stock_eval",
    "coin_eval",
    "cash_eval",
    "etf_eval",
    "equity_eval",
    "usd_krw",
    "weighted_leverage",
    "effective_exposure",
    "ltv",
    "vix",
]


def append_snapshot(
    summary: dict,
    all_rows: pd.DataFrame,
    usd_krw: float,
    *,
    now: datetime | None = None,
    leverage: dict | None = None,
    ratio: dict | None = None,
    vix: float | None = None,
) -> None:
    """오늘자 스냅샷을 기록. 같은 날짜면 덮어씀."""
    now = now or datetime.now()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    leverage = leverage or {}
    ratio = ratio or {}

    row = {
        "date": now.date().isoformat(),
        "ts": now.isoformat(timespec="seconds"),
        "total_eval": float(summary.get("total_eval", 0)),
        "gross_assets": float(summary.get("gross_assets", 0)),
        "debt": float(summary.get("debt", 0)),
        "total_cost": float(summary.get("total_cost", 0)),
        "total_pnl": float(summary.get("total_pnl", 0)),
        "total_return": float(summary.get("total_return", 0)),
        "stock_eval": _sum_where(all_rows, "분류", "해외주식"),
        "coin_eval": _sum_where(all_rows, "분류", "암호화폐"),
        "cash_eval": _sum_where(all_rows, "분류", "현금"),
        "etf_eval": _sum_where(all_rows, "종류", "ETF"),
        "equity_eval": _sum_where(all_rows, "종류", "개별주"),
        "usd_krw": float(usd_krw),
        "weighted_leverage": float(leverage.get("weighted", 0.0)),
        "effective_exposure": float(leverage.get("effective_exposure", 0.0)),
        "ltv": float(ratio.get("ltv", 0.0)),
        "vix": float(vix) if vix is not None else None,
    }

    if SNAPSHOT_FILE.exists():
        df = pd.read_csv(SNAPSHOT_FILE)
        df = df[df["date"] != row["date"]]  # 같은 날 기록 제거
        # 누락된 신규 컬럼 NaN으로 채워서 호환
        for c in _COLS:
            if c not in df.columns:
                df[c] = pd.NA
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row], columns=_COLS)

    df = df[[c for c in _COLS if c in df.columns]]
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(SNAPSHOT_FILE, index=False, encoding="utf-8-sig")


def load_snapshots() -> pd.DataFrame:
    """시계열 데이터 로드. 빈 DF가 컬럼만 가지고 반환될 수도 있음."""
    if not SNAPSHOT_FILE.exists():
        return pd.DataFrame(columns=_COLS)
    df = pd.read_csv(SNAPSHOT_FILE)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _sum_where(df: pd.DataFrame, col: str, value: str) -> float:
    if df.empty or col not in df.columns:
        return 0.0
    return float(df[df[col] == value]["평가금액"].sum())
