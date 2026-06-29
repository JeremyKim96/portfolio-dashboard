"""과거(엑셀 추출) + 현재(자동) 추이 데이터 통합.

- total_history.csv          : 총자산 추이 (엑셀 월별 + 현재부터 자동 누적)
- manual_account_history.csv : 계좌별 주간 추이 (엑셀, 과거 고정)
  → RAW 기반 account_history(현재)와 합쳐 연속 추이 생성.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from .config import HISTORY_DIR

TOTAL_HISTORY_FILE = HISTORY_DIR / "total_history.csv"
MANUAL_ACC_FILE = HISTORY_DIR / "manual_account_history.csv"


def load_total_history() -> pd.DataFrame:
    """총자산 추이. 반환 컬럼: date(datetime), total(float)."""
    if not TOTAL_HISTORY_FILE.exists():
        return pd.DataFrame(columns=["date", "total"])
    df = pd.read_csv(TOTAL_HISTORY_FILE)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["total"] = pd.to_numeric(df["total"], errors="coerce")
    df = df.dropna(subset=["date", "total"]).sort_values("date").reset_index(drop=True)
    return df


def append_total(managed: float, when: date | None = None) -> None:
    """현재 운용 총자산을 total_history 에 추가 (같은 날짜는 덮어씀). 로컬 전용."""
    if not managed or managed <= 0:
        return
    when = when or date.today()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    df = load_total_history()
    iso = pd.Timestamp(when)
    if not df.empty:
        df = df[df["date"].dt.date != when]
    df = pd.concat([df, pd.DataFrame([{"date": iso, "total": float(managed)}])],
                   ignore_index=True)
    df = df.sort_values("date")
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(TOTAL_HISTORY_FILE, index=False, encoding="utf-8-sig")


def load_manual_account_history() -> pd.DataFrame:
    """엑셀에서 추출한 계좌별 주간 추이. 컬럼: ts(datetime), account, eval_krw."""
    if not MANUAL_ACC_FILE.exists():
        return pd.DataFrame(columns=["ts", "account", "eval_krw"])
    df = pd.read_csv(MANUAL_ACC_FILE)
    if df.empty:
        return df
    df = df.rename(columns={"date": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df["eval_krw"] = pd.to_numeric(df["eval_krw"], errors="coerce")
    df = df.dropna(subset=["ts", "account", "eval_krw"])
    return df[["ts", "account", "eval_krw"]].reset_index(drop=True)


def combined_account_history(raw_acc_hist: pd.DataFrame | None) -> pd.DataFrame:
    """엑셀 과거 + RAW 현재 계좌별 추이 병합. 컬럼: ts, account, eval_krw.

    같은 (날짜, 계좌)는 RAW(현재) 우선.
    """
    manual = load_manual_account_history()
    frames = [f for f in (manual, raw_acc_hist) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=["ts", "account", "eval_krw"])
    out = pd.concat(frames, ignore_index=True)
    out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
    out = out.dropna(subset=["ts", "account", "eval_krw"])
    # 같은 날짜·계좌 중복 → 마지막(=RAW, 뒤에 concat됨) 우선
    out["_d"] = out["ts"].dt.date
    out = out.drop_duplicates(subset=["_d", "account"], keep="last").drop(columns="_d")
    return out.sort_values("ts").reset_index(drop=True)
