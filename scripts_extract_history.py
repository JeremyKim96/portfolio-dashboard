"""과거 자산관리 엑셀에서 총자산·계좌별 추이 데이터를 추출.

- 전체 자산 시트 → history/total_history.csv  [date, total]  (월별)
- 각 계좌 시트   → history/manual_account_history.csv  [date, account, eval_krw] (주간)

잘못된/빈 값은 스킵. 한 번 실행으로 history/ 에 CSV 생성.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

SRC = r"C:\Users\ksw96\Desktop\자산관리\자산관리_김승환_6.03.xlsx"
HIST = Path(__file__).parent / "history"
HIST.mkdir(exist_ok=True)

wb = load_workbook(SRC, data_only=True)


def to_num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(",", "").strip()
    if not s or s in ("-", "—", "#N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(x):
    if isinstance(x, datetime):
        return x.date()
    if x is None:
        return None
    s = str(x).strip()
    m = re.match(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3])).date()
        except ValueError:
            return None
    return None


# ---------- 1) 전체 자산: 월별 총자산 ----------
ws = wb["전체 자산"]
rows = list(ws.iter_rows(values_only=True))
total_rows = []
in_section = False
for r in rows:
    cells = list(r)
    label = str(cells[1]).strip() if len(cells) > 1 and cells[1] else ""
    if "월별 자산" in label:
        in_section = True
        continue
    if in_section:
        d = parse_date(cells[2] if len(cells) > 2 else None)  # 날짜 (col C)
        amt = to_num(cells[4] if len(cells) > 4 else None)    # 금액 (col E)
        if d and amt and amt > 0:
            total_rows.append({"date": d.isoformat(), "total": amt})

total_df = pd.DataFrame(total_rows).drop_duplicates("date").sort_values("date")
total_df.to_csv(HIST / "total_history.csv", index=False, encoding="utf-8-sig")
print(f"[총자산] {len(total_df)}개 시점")
print(total_df.to_string(index=False))


# ---------- 2) 계좌별: 주간 평가금액 ----------
ACCOUNT_SHEETS = {
    "미국주식(1)": "미국주식①",
    "미국주식(2)": "미국주식②",
    "미국주식(3)": "미국주식③",
    "한국주식": "한국주식",
    "코인": "코인",
}

acc_rows = []
for sheet, acc_name in ACCOUNT_SHEETS.items():
    if sheet not in wb.sheetnames:
        continue
    ws = wb[sheet]
    in_section = False
    for r in ws.iter_rows(values_only=True):
        cells = list(r)
        label = str(cells[1]).strip() if len(cells) > 1 and cells[1] else ""
        if "주간 자산 평가금액" in label:
            in_section = True
            continue
        if in_section:
            # 헤더행("날짜") 스킵
            if label == "날짜":
                continue
            d = parse_date(cells[1] if len(cells) > 1 else None)   # 날짜 (col B)
            amt = to_num(cells[2] if len(cells) > 2 else None)     # 총 금액 (col C)
            if d and amt is not None and amt > 0:
                acc_rows.append({"date": d.isoformat(), "account": acc_name, "eval_krw": amt})

acc_df = pd.DataFrame(acc_rows).drop_duplicates(["date", "account"]).sort_values(["date", "account"])
acc_df.to_csv(HIST / "manual_account_history.csv", index=False, encoding="utf-8-sig")
print(f"\n[계좌별] {len(acc_df)}행, 계좌={sorted(acc_df['account'].unique())}")
piv = acc_df.pivot_table(index="date", columns="account", values="eval_krw", aggfunc="last")
print(piv.tail(8).to_string())
