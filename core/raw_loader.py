"""RAW 자산 엑셀 로더.

사용자가 신한·업비트에서 추출한 통합 RAW 파일을 읽어 표준화된
보유내역(holdings) + 예수금(cash) 데이터프레임으로 변환한다.

RAW 파일 컬럼 (16개, 한글 헤더):
    기준일시 / 계좌번호 / 계좌별칭 / 계좌유형 / 구분 / 항목 / 티커 /
    보유수량 / 매수평균가(원) / 현재가(원) / 매수금액(원) / 평가금액(원) /
    평가손익(원) / 수익률(%) / 통화 / 비고

구분(行 종류)으로 분류:
    - "보유종목"    → 실제 종목 (티커 있음)
    - "예수금"      → 계좌별 현금
    - "매매손익"    → 손익 요약 (스킵)
    - "환차손익"    → 환차 요약 (스킵)
    - "계좌요약"    → 계좌별 합계 (스킵)
    - "전체요약"    → 최상단 총계 (스킵)
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import RAW_DIR

# 파일명에서 _YYYYMMDD_HHMM 또는 _YYYYMMDD_HHMMSS 추출
_TS_PATTERN = re.compile(r"_(\d{8})_(\d{4,6})")

# RAW 원본 → 표준 영문 컬럼 매핑
_COL_MAP = {
    "기준일시": "ts",
    "계좌번호": "account_no",
    "계좌별칭": "account_alias",
    "계좌유형": "account_type",
    "구분": "section",
    "항목": "item",
    "티커": "ticker",
    "보유수량": "qty",
    "매수평균가(원)": "avg_price_krw",
    "현재가(원)": "current_price_krw",
    "매수금액(원)": "cost_krw",
    "평가금액(원)": "eval_krw",
    "평가손익(원)": "pnl_krw",
    "수익률(%)": "return_pct",
    "통화": "currency",
    "비고": "memo",
}

SECTION_HOLDING = "보유종목"
SECTION_CASH = "예수금"
SECTION_ACCOUNT_SUMMARY = "계좌요약"
SECTION_TRADE_PNL = "매매손익"
SECTION_FX_PNL = "환차손익"
SECTION_OVERALL = "전체요약"
SECTION_SKIP = {SECTION_ACCOUNT_SUMMARY, SECTION_TRADE_PNL, SECTION_FX_PNL, SECTION_OVERALL}


def _name_ts(path: Path) -> tuple[int, int]:
    """파일명 `_YYYYMMDD_HHMM(SS)` 추출 → (yyyymmdd, hhmmss) 정수.

    패턴 없으면 (0, 0) → mtime 기준으로 fallback 정렬됨.
    """
    m = _TS_PATTERN.search(path.stem)
    if not m:
        return (0, 0)
    date_str, time_str = m.group(1), m.group(2)
    try:
        date_int = int(date_str)
        # HHMM 또는 HHMMSS 둘 다 지원 → HHMMSS 정규화 (HHMM이면 *100)
        time_int = int(time_str) * 100 if len(time_str) == 4 else int(time_str)
        return (date_int, time_int)
    except ValueError:
        return (0, 0)


def latest_raw_path(folder: Path = RAW_DIR) -> Path | None:
    """RAW 폴더에서 가장 최근 .xlsx 반환.

    정렬 우선순위:
    1. 파일명 안의 `_YYYYMMDD_HHMM(SS)` 타임스탬프 (동일 날짜면 늦은 시간)
    2. 패턴 없으면 파일 수정 시각(mtime)
    """
    if not folder.exists():
        return None
    xls = [p for p in folder.glob("*.xlsx") if not p.name.startswith("~$")]
    if not xls:
        return None
    return max(xls, key=lambda p: (_name_ts(p), p.stat().st_mtime))


def all_raw_paths(folder: Path = RAW_DIR) -> list[Path]:
    """RAW 폴더의 모든 .xlsx를 시간순(과거→최신)으로 반환."""
    if not folder.exists():
        return []
    xls = [p for p in folder.glob("*.xlsx") if not p.name.startswith("~$")]
    return sorted(xls, key=lambda p: (_name_ts(p), p.stat().st_mtime))


def account_history(folder: Path = RAW_DIR) -> pd.DataFrame:
    """RAW 폴더 전체를 시계열로 읽어 계좌별 평가액 추이를 만든다.

    각 RAW 파일 = 한 시점의 스냅샷. 파일별로 계좌(account_alias)별
    평가금액(보유종목 + 예수금)을 합산하여 long-format 반환.

    반환 컬럼: ts(datetime), account(str), eval_krw(float)
    동일 기준일시가 여러 파일에 있으면 가장 늦게 정렬된 파일 값 사용.
    """
    paths = all_raw_paths(folder)
    if not paths:
        return pd.DataFrame(columns=["ts", "account", "eval_krw"])

    rows: list[dict] = []
    seen_ts: dict = {}
    for p in paths:
        try:
            raw = load_raw(p)
        except Exception:
            continue
        if raw.empty:
            continue
        ts = raw_timestamp(raw)
        if ts is None:
            # 파일명 타임스탬프 fallback
            d, t = _name_ts(p)
            if d:
                try:
                    ts = datetime.strptime(f"{d}{t:06d}", "%Y%m%d%H%M%S")
                except ValueError:
                    ts = datetime.fromtimestamp(p.stat().st_mtime)
            else:
                ts = datetime.fromtimestamp(p.stat().st_mtime)

        # 보유종목 + 예수금만 (요약행 제외), 계좌별 합산
        df = raw[raw["section"].isin([SECTION_HOLDING, SECTION_CASH])].copy()
        if df.empty:
            continue
        df["account_alias"] = df["account_alias"].replace("", "기타")
        grp = df.groupby("account_alias")["eval_krw"].sum()

        ts_key = ts.strftime("%Y-%m-%d %H:%M")
        # 동일 기준일시는 마지막(나중 정렬) 값으로 덮어씀
        seen_ts[ts_key] = (ts, grp)

    for ts_key, (ts, grp) in seen_ts.items():
        for account, val in grp.items():
            rows.append({"ts": ts, "account": account, "eval_krw": float(val)})

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("ts").reset_index(drop=True)


def raw_info(folder: Path = RAW_DIR) -> dict:
    """사이드바 표시용 RAW 파일 메타."""
    p = latest_raw_path(folder)
    if p is None:
        return {"path": None, "name": None, "mtime": None, "folder": str(folder)}
    return {
        "path": p,
        "name": p.name,
        "mtime": datetime.fromtimestamp(p.stat().st_mtime),
        "folder": str(folder),
    }


def load_raw(path: Path | None = None) -> pd.DataFrame:
    """RAW 엑셀 로드 → 표준 컬럼 DF.

    반환 컬럼:
        ts, account_no, account_alias, asset_class, section, item, ticker,
        qty, avg_price_krw, current_price_krw, cost_krw, eval_krw,
        pnl_krw, return_pct, currency, memo

    빈 행/요약 행은 그대로 둠 (호출자가 section으로 필터링).
    """
    if path is None:
        path = latest_raw_path()
    if path is None:
        return _empty_raw()

    df = pd.read_excel(path, engine="openpyxl")
    # 첫 시트만 사용. 헤더가 정확히 한국어 16개라고 가정.
    df = df.rename(columns=_COL_MAP)

    needed = list(_COL_MAP.values())
    for c in needed:
        if c not in df.columns:
            df[c] = None
    df = df[needed]

    # 숫자 컬럼 강제 변환
    for c in ["qty", "avg_price_krw", "current_price_krw",
              "cost_krw", "eval_krw", "pnl_krw", "return_pct"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 문자열 정리
    for c in ["account_no", "account_alias", "account_type", "section",
              "item", "ticker", "currency", "memo"]:
        df[c] = df[c].astype(str).where(df[c].notna(), "")
        df[c] = df[c].str.strip()
        df[c] = df[c].replace({"nan": "", "None": ""})

    df["ticker"] = df["ticker"].str.upper()
    return df.reset_index(drop=True)


def split_holdings_and_cash(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """RAW DF → (보유종목, 예수금) 분리."""
    if raw.empty:
        return raw, raw

    holdings = raw[raw["section"] == SECTION_HOLDING].copy()
    holdings = holdings[holdings["ticker"] != ""]
    # 수량 0 또는 평가금액 0인 더미 행 제거 (USDT 잔돈 등은 유지)
    holdings = holdings[holdings["qty"].fillna(0) > 0]

    cash = raw[raw["section"] == SECTION_CASH].copy()

    return holdings.reset_index(drop=True), cash.reset_index(drop=True)


def raw_timestamp(raw: pd.DataFrame) -> datetime | None:
    """RAW 데이터의 '데이터일시' 중 가장 최근 값."""
    if raw.empty or "ts" not in raw.columns:
        return None
    try:
        s = pd.to_datetime(raw["ts"], errors="coerce").dropna()
        if s.empty:
            return None
        return s.max().to_pydatetime()
    except Exception:
        return None


def _empty_raw() -> pd.DataFrame:
    return pd.DataFrame(columns=list(_COL_MAP.values()))
