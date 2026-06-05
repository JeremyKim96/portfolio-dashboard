"""데이터 소스 추상화 — 로컬(PC) vs 클라우드(Streamlit Cloud).

- 로컬(PC): RAW 폴더 + portfolio.xlsx 에서 직접 계산.
- 클라우드: PC가 push 한 cloud_data/portfolio_data.json 을 읽어 동일한
  DataFrame 형태로 복원 (읽기 전용 뷰).

시장지표·뉴스·버블은 양쪽 모두 실시간으로 외부 API에서 가져온다(여기서 다루지 않음).
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import RAW_DIR, ROOT

CLOUD_DATA_DIR = ROOT / "cloud_data"
BUNDLE_PATH = CLOUD_DATA_DIR / "portfolio_data.json"


def is_cloud() -> bool:
    """클라우드(Streamlit Cloud) 실행 여부.

    - 환경변수 CLOUD_MODE=1 이면 강제 클라우드
    - 그 외엔 RAW 폴더(Windows 경로) 존재 여부로 판단 (리눅스 클라우드엔 없음)
    """
    if os.environ.get("CLOUD_MODE") == "1":
        return True
    try:
        import streamlit as st
        if st.secrets.get("CLOUD_MODE") in ("1", 1, True, "true", "True"):
            return True
    except Exception:
        pass
    return not RAW_DIR.exists()


# ---------- 로컬: RAW 에서 직접 계산 ----------

def build_local_data() -> dict:
    from .raw_loader import (account_history, load_raw, raw_info,
                             raw_timestamp, split_holdings_and_cash)
    from .loaders import load_family, load_meta
    from .calc import build_rows_from_raw
    from .snapshots import load_snapshots

    raw_df = load_raw()
    meta_df = load_meta()
    family_df = load_family()
    raw_meta = raw_info()

    if raw_df.empty:
        return {"empty": True, "raw_meta": raw_meta, "family_df": family_df,
                "meta_df": meta_df}

    holdings_df, cash_df_raw = split_holdings_and_cash(raw_df)
    stock_rows, coin_rows, cash_rows = build_rows_from_raw(holdings_df, cash_df_raw, meta_df)

    return {
        "empty": False,
        "source": "local",
        "stock_rows": stock_rows,
        "coin_rows": coin_rows,
        "cash_rows": cash_rows,
        "family_df": family_df,
        "meta_df": meta_df,
        "account_hist": account_history(),
        "snapshots": load_snapshots(),
        "raw_meta": raw_meta,
        "raw_ts": raw_timestamp(raw_df),
        "generated_at": datetime.now(),
    }


# ---------- export: 로컬 데이터를 JSON 번들로 ----------

def _df_to_json(df: pd.DataFrame) -> str:
    return df.to_json(orient="split", force_ascii=False, date_format="iso")


def _df_from_json(s: str, datetime_cols: list[str] | None = None) -> pd.DataFrame:
    df = pd.read_json(io.StringIO(s), orient="split")
    for c in (datetime_cols or []):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def export_bundle() -> Path:
    """로컬 데이터를 cloud_data/portfolio_data.json 으로 저장. (PC에서 실행)"""
    data = build_local_data()
    CLOUD_DATA_DIR.mkdir(parents=True, exist_ok=True)

    if data.get("empty"):
        bundle = {"empty": True, "generated_at": datetime.now().isoformat()}
    else:
        bundle = {
            "empty": False,
            "generated_at": data["generated_at"].isoformat(),
            "raw_ts": data["raw_ts"].isoformat() if data.get("raw_ts") else None,
            "raw_name": data["raw_meta"].get("name"),
            "stock_rows": _df_to_json(data["stock_rows"]),
            "coin_rows": _df_to_json(data["coin_rows"]),
            "cash_rows": _df_to_json(data["cash_rows"]),
            "family_df": _df_to_json(data["family_df"]),
            "meta_df": _df_to_json(data["meta_df"]),
            "account_hist": _df_to_json(data["account_hist"]),
            "snapshots": _df_to_json(data["snapshots"]),
        }
    BUNDLE_PATH.write_text(json.dumps(bundle, ensure_ascii=False, indent=0),
                           encoding="utf-8")
    return BUNDLE_PATH


# ---------- 클라우드: JSON 번들 로드 ----------

def load_bundle() -> dict:
    if not BUNDLE_PATH.exists():
        return {"empty": True, "source": "cloud",
                "raw_meta": {"name": None, "folder": "(클라우드)", "mtime": None}}

    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    if bundle.get("empty"):
        return {"empty": True, "source": "cloud",
                "raw_meta": {"name": None, "folder": "(클라우드)", "mtime": None}}

    gen = datetime.fromisoformat(bundle["generated_at"])
    raw_ts = datetime.fromisoformat(bundle["raw_ts"]) if bundle.get("raw_ts") else None
    return {
        "empty": False,
        "source": "cloud",
        "stock_rows": _df_from_json(bundle["stock_rows"]),
        "coin_rows": _df_from_json(bundle["coin_rows"]),
        "cash_rows": _df_from_json(bundle["cash_rows"]),
        "family_df": _df_from_json(bundle["family_df"]),
        "meta_df": _df_from_json(bundle["meta_df"]),
        "account_hist": _df_from_json(bundle["account_hist"], ["ts"]),
        "snapshots": _df_from_json(bundle["snapshots"], ["date"]),
        "raw_meta": {"name": bundle.get("raw_name"), "folder": "(클라우드)", "mtime": gen},
        "raw_ts": raw_ts,
        "generated_at": gen,
    }


def get_dashboard_data() -> dict:
    """실행 환경에 맞는 포트폴리오 데이터 dict 반환."""
    if is_cloud():
        return load_bundle()
    return build_local_data()
