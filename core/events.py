"""다가오는 핵심 일정 — 미국 거시지표·연준·실적(반도체 중심)·옵션만기·코인·휴장.

데이터 원천 (모두 공개·무료, 개인정보 전송 없음):
  1) 확정 일정(큐레이션): 연준 FOMC·고용·CPI·PCE·휴장 등 공식 발표 일정
  2) 규칙 일정: 옵션 만기(셋째 금요일)·쿼드러플위칭·코인 옵션 만기(마지막 금요일)·
     TSMC 월매출(10일 전후)·고용보고서(첫째 금요일)·FOMC 의사록(결정 3주 뒤)
  3) 이번주 경제지표: ForexFactory 공개 캘린더 JSON (예상치·이전치 포함)
  4) 실적 발표일: yfinance (반도체·빅테크·코인 관련주 + 내 보유 종목)

모든 시각은 한국시간(KST)으로 변환해서 반환. 실패한 원천은 조용히 건너뜀.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests
import streamlit as st

KST = timezone(timedelta(hours=9))
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ── 카테고리 → 아이콘 ──
CAT_ICON = {
    "금리": "🏦", "물가": "🔥", "고용": "👷", "경기": "📊", "실적": "💼",
    "만기": "🎯", "휴장": "🏖️", "코인": "🪙", "연설": "🎤", "반도체": "🔌",
}

# ─────────────────────────────────────────────────────────────
#  1) 확정 일정 (공식 발표 기준, 미국 동부시간 ET)
#     출처: federalreserve.gov FOMC 캘린더(2026-11-12 개정), BLS/BEA 발표 일정
# ─────────────────────────────────────────────────────────────
# (날짜, ET시각 or None, 제목, 카테고리, 중요도1~3, 메모, 태그)
_CURATED: list[tuple] = [
    ("2026-11-25", "08:30", "10월 PCE 물가지수 (연준이 가장 중시하는 물가)", "물가", 3,
     "물가 재상승 여부 → 12월 금리 인상 경계감에 직결", ["금리"]),
    ("2026-11-26", None, "추수감사절 — 미국 증시 휴장", "휴장", 1, "코인 시장은 24시간 거래", []),
    ("2026-11-27", None, "블랙프라이데이 — 미국 증시 조기폐장 (13:00 ET)", "휴장", 1,
     "거래량 적어 변동성 왜곡 가능", []),
    ("2026-12-04", "08:30", "11월 고용보고서 (비농업고용·실업률·임금)", "고용", 3,
     "FOMC 직전 마지막 고용지표", ["금리"]),
    ("2026-12-09", "14:00", "FOMC 금리 결정 + 연준 의장 기자회견", "금리", 3,
     "기자회견은 30분 뒤 · 금리 인상 여부가 최대 변수 · 점도표(내년 금리 경로) 발표", ["금리"]),
    ("2026-12-10", "08:30", "11월 CPI 소비자물가", "물가", 3,
     "FOMC 바로 다음날 — 물가 서프라이즈 시 변동성 확대", ["금리"]),
    ("2026-12-15", "08:30", "11월 PPI 생산자물가", "물가", 2, "", []),
    ("2026-12-23", "08:30", "11월 PCE 물가 + 3분기 GDP 확정치", "물가", 2, "", ["금리"]),
    ("2026-12-24", None, "크리스마스 이브 — 미국 증시 조기폐장 (13:00 ET)", "휴장", 1, "", []),
    ("2026-12-25", None, "크리스마스 — 미국 증시 휴장", "휴장", 1, "", []),
    ("2027-01-01", None, "신정 — 미국 증시 휴장", "휴장", 1, "", []),
    ("2027-01-18", None, "마틴 루터 킹 데이 — 미국 증시 휴장", "휴장", 1, "", []),
    ("2027-02-15", None, "대통령의 날 — 미국 증시 휴장", "휴장", 1, "", []),
]

# 2027 FOMC (결정일 = 회의 둘째 날). 10/27 은 전략 전용 회의(금리 결정 없음)
_FOMC_DECISIONS = ["2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
                   "2027-07-28", "2027-09-15", "2027-11-17"]
_FOMC_EXTRA = [("2027-10-27", "FOMC 전략 회의 (금리 결정 없음)")]

# 실적 발표 기본값 (yfinance 조회 실패 시 폴백) — 미국 날짜, 장전/장후 미정
_EARNINGS_FALLBACK = {
    "AVGO": "2026-12-10", "ADBE": "2026-12-10", "ORCL": "2026-12-15", "MU": "2026-12-18",
}

# ── 실적 관심 종목 (반도체 우선) ──
SEMI = ["NVDA", "AMD", "AVGO", "MU", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "INTC",
        "QCOM", "MRVL", "ARM"]
BIGTECH = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "ORCL", "ADBE", "PLTR",
           "CRM", "NFLX"]
CRYPTO_EQ = ["COIN", "MSTR"]
_NAME_KO = {
    "NVDA": "엔비디아", "AMD": "AMD", "AVGO": "브로드컴", "MU": "마이크론", "TSM": "TSMC",
    "ASML": "ASML", "AMAT": "어플라이드머티리얼즈", "LRCX": "램리서치", "KLAC": "KLA",
    "INTC": "인텔", "QCOM": "퀄컴", "MRVL": "마벨", "ARM": "ARM",
    "AAPL": "애플", "MSFT": "마이크로소프트", "GOOGL": "알파벳(구글)", "AMZN": "아마존",
    "META": "메타", "TSLA": "테슬라", "ORCL": "오라클", "ADBE": "어도비", "PLTR": "팔란티어",
    "CRM": "세일즈포스", "NFLX": "넷플릭스", "COIN": "코인베이스", "MSTR": "스트래티지",
}
_TOP_SEMI = {"NVDA", "AVGO", "MU", "TSM", "ASML", "AMD"}
_MAG7 = {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA"}
# 나스닥100 편입 종목 (QQQ·QLD·TQQQ 에 직접 영향) — ORCL·CRM·TSM 은 NYSE 상장이라 제외
_NDX100 = _MAG7 | {"ADBE", "PLTR", "NFLX", "AMD", "AVGO", "MU", "ASML", "AMAT", "LRCX",
                   "KLAC", "INTC", "QCOM", "MRVL", "ARM"}


# ─────────────────────────────────────────────────────────────
#  시간 유틸 (zoneinfo 없이 — 윈도우 tzdata 의존 제거)
# ─────────────────────────────────────────────────────────────
def _nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    """y년 m월의 n번째 weekday(월=0). n=-1 이면 마지막."""
    if n > 0:
        d = date(y, m, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _et_utc_offset(d: date) -> int:
    """미국 동부시간 UTC 오프셋 (서머타임: 3월 둘째 일요일 ~ 11월 첫째 일요일)."""
    start = _nth_weekday(d.year, 3, 6, 2)
    end = _nth_weekday(d.year, 11, 6, 1)
    return -4 if start <= d < end else -5


def _et_to_kst(d: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    et = datetime(d.year, d.month, d.day, h, m, tzinfo=timezone(timedelta(hours=_et_utc_offset(d))))
    return et.astimezone(KST)


def today_kst() -> date:
    return datetime.now(KST).date()


def _ev(d: date, hhmm: str | None, title: str, cat: str, imp: int, note: str = "",
        tags: list[str] | None = None, src: str = "확정", ticker: str = "") -> dict:
    if hhmm:
        kst = _et_to_kst(d, hhmm)
        k_date, k_time = kst.date(), kst.strftime("%H:%M")
    else:
        k_date, k_time = d, ""
    return {"date": k_date, "time": k_time, "us_date": d, "title": title, "cat": cat,
            "imp": int(imp), "note": note, "tags": list(tags or []), "src": src,
            "ticker": ticker}


# ─────────────────────────────────────────────────────────────
#  1) 확정 + 2) 규칙 일정
# ─────────────────────────────────────────────────────────────
def _curated_events() -> list[dict]:
    out = []
    for ds, t, title, cat, imp, note, tags in _CURATED:
        out.append(_ev(date.fromisoformat(ds), t, title, cat, imp, note, tags))
    for ds in _FOMC_DECISIONS[1:]:  # 12/9 는 위에 상세 항목으로 있음
        out.append(_ev(date.fromisoformat(ds), "14:00", "FOMC 금리 결정 + 기자회견", "금리", 3,
                       "회의 둘째 날 발표", ["금리"]))
    for ds, title in _FOMC_EXTRA:
        out.append(_ev(date.fromisoformat(ds), "14:00", title, "금리", 1, "", ["금리"]))
    # FOMC 의사록 = 결정 3주 뒤 수요일 14:00 ET
    for ds in _FOMC_DECISIONS:
        d = date.fromisoformat(ds) + timedelta(days=21)
        out.append(_ev(d, "14:00", "FOMC 의사록 공개", "금리", 2,
                       f"{ds[5:].replace('-', '/')} 회의 내용 — 위원들 성향 확인", ["금리"],
                       src="규칙"))
    return out


def _rule_events(start: date, end: date) -> list[dict]:
    out = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        # 옵션 만기 (셋째 금요일) / 분기월은 쿼드러플 위칭
        opex = _nth_weekday(y, m, 4, 3)
        if m in (3, 6, 9, 12):
            out.append(_ev(opex, None, "쿼드러플 위칭 (주가지수·개별주 선물·옵션 동시만기)", "만기", 2,
                           "대규모 포지션 정리로 장 막판 변동성 확대 경향", ["반도체", "나스닥"], src="규칙"))
        else:
            out.append(_ev(opex, None, "미국 월간 옵션 만기", "만기", 1, "", [], src="규칙"))
        # 코인 옵션 만기 (Deribit, 마지막 금요일 08:00 UTC = 17:00 KST)
        last_fri = _nth_weekday(y, m, 4, -1)
        q = m in (3, 6, 9, 12)
        out.append({**_ev(last_fri, None,
                          "코인 옵션 " + ("분기물" if q else "월물") + " 만기 (Deribit)",
                          "코인", 2 if q else 1, "BTC·ETH 옵션 만기 — 만기 전후 가격 쏠림 주의",
                          ["코인"], src="규칙"), "time": "17:00"})
        # TSMC 월매출 (대만, 10일 전후)
        out.append(_ev(date(y, m, 10), None, "TSMC 월간 매출 발표 (10일 전후)", "반도체", 1,
                       "AI 반도체 수요 바로미터", ["반도체"], src="규칙"))
        # 고용보고서 — 첫째 금요일 (1일이 휴일이면 둘째 주). 12월분은 확정 일정 사용
        if not (y == 2026 and m == 12):
            fr = _nth_weekday(y, m, 4, 1)
            if fr.day == 1 and m == 1:
                fr += timedelta(days=7)
            out.append(_ev(fr, "08:30", "고용보고서 (비농업고용·실업률) — 예상 일정", "고용", 3,
                           "BLS 확정 전 예상 날짜", ["금리"], src="규칙"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return [e for e in out if start <= e["us_date"] <= end]


# ─────────────────────────────────────────────────────────────
#  3) 이번주 경제지표 (ForexFactory 공개 JSON)
# ─────────────────────────────────────────────────────────────
_FF_KO = {
    "CPI m/m": "CPI 소비자물가 (전월비)", "CPI y/y": "CPI 소비자물가 (전년비)",
    "Core CPI m/m": "근원 CPI (전월비)", "Core CPI y/y": "근원 CPI (전년비)",
    "Non-Farm Employment Change": "비농업 고용", "Unemployment Rate": "실업률",
    "Average Hourly Earnings m/m": "평균 시간당 임금",
    "Federal Funds Rate": "연준 기준금리 결정", "FOMC Statement": "FOMC 성명서",
    "FOMC Press Conference": "FOMC 기자회견", "FOMC Meeting Minutes": "FOMC 의사록",
    "FOMC Economic Projections": "FOMC 경제전망(점도표)",
    "Core PCE Price Index m/m": "근원 PCE 물가", "PCE Price Index m/m": "PCE 물가",
    "Retail Sales m/m": "소매판매", "Core Retail Sales m/m": "근원 소매판매",
    "PPI m/m": "PPI 생산자물가", "Core PPI m/m": "근원 PPI",
    "Advance GDP q/q": "GDP 속보치", "Prelim GDP q/q": "GDP 잠정치", "Final GDP q/q": "GDP 확정치",
    "ISM Manufacturing PMI": "ISM 제조업 PMI", "ISM Services PMI": "ISM 서비스업 PMI",
    "JOLTS Job Openings": "JOLTS 구인건수", "ADP Non-Farm Employment Change": "ADP 민간고용",
    "Unemployment Claims": "신규 실업수당 청구", "CB Consumer Confidence": "소비자신뢰지수",
    "Prelim UoM Consumer Sentiment": "미시간대 소비자심리 (예비)",
    "Revised UoM Consumer Sentiment": "미시간대 소비자심리 (확정)",
    "Prelim UoM Inflation Expectations": "미시간대 기대인플레 (예비)",
    "Revised UoM Inflation Expectations": "미시간대 기대인플레 (확정)",
    "Philly Fed Manufacturing Index": "필라델피아 연은 제조업지수",
    "Empire State Manufacturing Index": "뉴욕 연은 제조업지수",
    "Durable Goods Orders m/m": "내구재 주문", "Core Durable Goods Orders m/m": "근원 내구재 주문",
    "Pending Home Sales m/m": "잠정 주택판매", "New Home Sales": "신규 주택판매",
    "Existing Home Sales": "기존 주택판매", "Flash Manufacturing PMI": "제조업 PMI (예비)",
    "Flash Services PMI": "서비스업 PMI (예비)", "Trade Balance": "무역수지",
    "Employment Cost Index q/q": "고용비용지수",
}
_SPEAK_RE = re.compile(r"^(Fed Chair(?:man)?|FOMC Member|President|Treasury Secretary)\s+(.+?)\s+Speaks$")
_SPEAKER_KO = {"Fed Chair": "연준 의장", "Fed Chairman": "연준 의장", "FOMC Member": "FOMC 위원",
               "President": "대통령", "Treasury Secretary": "재무장관"}


def _ff_category(title: str) -> str:
    t = title.lower()
    if "speaks" in t:
        return "연설"
    if any(k in t for k in ("fomc", "funds rate")):
        return "금리"
    if any(k in t for k in ("cpi", "ppi", "pce", "inflation")):
        return "물가"
    if any(k in t for k in ("employment", "unemployment", "payroll", "jolts", "earnings m/m",
                            "claims")):
        return "고용"
    return "경기"


def _ff_title_ko(title: str) -> str:
    if title in _FF_KO:
        return _FF_KO[title]
    m = _SPEAK_RE.match(title)
    if m:
        return f"{m.group(2)} {_SPEAKER_KO.get(m.group(1), m.group(1))} 연설"
    return title


@st.cache_data(ttl=3600, show_spinner=False)
def _ff_week(which: str) -> list[dict]:
    try:
        r = requests.get(f"https://nfs.faireconomy.media/ff_calendar_{which}.json",
                         headers=_UA, timeout=8)
        r.raise_for_status()
        raw = r.json()
    except Exception:
        return []
    out = []
    for x in raw:
        if x.get("country") != "USD" or x.get("impact") not in ("High", "Medium"):
            continue
        try:
            dt = datetime.fromisoformat(str(x["date"]))
        except Exception:
            continue
        kst = dt.astimezone(KST)
        title = str(x.get("title", ""))
        fc, pv = str(x.get("forecast") or ""), str(x.get("previous") or "")
        note = " · ".join(s for s in [f"예상 {fc}" if fc else "", f"이전 {pv}" if pv else ""] if s)
        cat = _ff_category(title)
        out.append({
            "date": kst.date(), "time": kst.strftime("%H:%M"), "us_date": dt.date(),
            "title": _ff_title_ko(title), "cat": cat,
            "imp": 3 if x.get("impact") == "High" else 2,
            "note": note, "tags": ["금리"] if cat in ("금리", "물가", "고용") else [],
            "src": "경제캘린더", "ticker": "", "forecast": fc, "previous": pv,
            "actual": str(x.get("actual") or ""), "title_en": title,
        })
    return out


def economic_calendar_this_week() -> list[dict]:
    """이번주(+가능하면 다음주) 미국 주요 경제지표. 예상치·이전치 포함."""
    items = _ff_week("thisweek") + _ff_week("nextweek")
    seen, out = set(), []
    for e in sorted(items, key=lambda e: (e["date"], e["time"])):
        k = (e["date"], e["time"], e["title"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


# ─────────────────────────────────────────────────────────────
#  4) 실적 발표일 (yfinance)
# ─────────────────────────────────────────────────────────────
def _one_earnings(tk: str) -> tuple[str, list]:
    try:
        import yfinance as yf
        cal = yf.Ticker(tk).calendar or {}
        ds = cal.get("Earnings Date") or []
        return tk, [d for d in ds if isinstance(d, date)]
    except Exception:
        return tk, []


@st.cache_data(ttl=43200, show_spinner=False)
def _earnings_dates(tickers: tuple[str, ...]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for tk, ds in ex.map(_one_earnings, tickers):
            if ds:
                out[tk] = [d.isoformat() for d in ds]
    return out


def earnings_events(holding_tickers: set[str] | None = None, fetch: bool = True) -> list[dict]:
    holding_tickers = {t.upper() for t in (holding_tickers or set())}
    stock_holdings = {t for t in holding_tickers
                      if t.isalpha() and t not in {"ETH", "BTC", "USDT", "XRP", "SOL"}}
    etf_like = {"SOXL", "TQQQ", "QLD", "QQQ", "MAGS", "IGV", "SPY", "SOXX", "SMH"}
    universe = tuple(sorted(set(SEMI + BIGTECH + CRYPTO_EQ) | (stock_holdings - etf_like)))
    fetched = _earnings_dates(universe) if fetch else {}

    out = []
    for tk in universe:
        ds = fetched.get(tk)
        approx = False
        if not ds and tk in _EARNINGS_FALLBACK:
            ds = [_EARNINGS_FALLBACK[tk]]
        if not ds:
            continue
        if len(ds) > 1:
            approx = True
        d = date.fromisoformat(ds[0])
        name = _NAME_KO.get(tk, tk)
        is_semi = tk in SEMI
        mine = tk in holding_tickers
        imp = 3 if (tk in _TOP_SEMI or tk in {"AAPL", "MSFT", "GOOGL", "AMZN", "META"}) else 2
        if mine:
            imp = 3
        tags = []
        if is_semi:
            tags.append("반도체")
        if tk in _NDX100:
            tags.append("나스닥")
        if tk in CRYPTO_EQ:
            tags.append("코인")
        if mine:
            tags.append("내 보유")
        note_bits = []
        if is_semi and "SOXL" in holding_tickers:
            note_bits.append("SOXL(반도체 3배) 직접 영향")
        elif tk in _MAG7 and holding_tickers & {"QQQ", "QLD", "TQQQ", "MAGS"}:
            note_bits.append("나스닥 ETF(QQQ·QLD·TQQQ·MAGS) 영향")
        elif tk in _NDX100 and holding_tickers & {"QQQ", "QLD", "TQQQ"}:
            note_bits.append("나스닥100 편입 종목 → QQQ 계열 영향")
        note_bits.append("미국 날짜 기준 · 장전/장후 미정" + (" · 예상(범위)" if approx else ""))
        out.append(_ev(d, None, f"{name} ({tk}) 실적 발표", "실적", imp,
                       " · ".join(note_bits), tags, src="실적", ticker=tk))
    return out


# ─────────────────────────────────────────────────────────────
#  통합
# ─────────────────────────────────────────────────────────────
def upcoming_events(days: int = 60, holding_tickers: set[str] | None = None,
                    include_network: bool = True) -> list[dict]:
    """오늘(KST)부터 days일 이내 일정. include_network=False면 네트워크 없이 확정+규칙만."""
    start = today_kst() - timedelta(days=1)
    end = today_kst() + timedelta(days=days)
    evs = _curated_events() + _rule_events(start, end + timedelta(days=1))
    evs += earnings_events(holding_tickers, fetch=include_network)

    if include_network:
        ff = economic_calendar_this_week()
        # 확정 일정과 같은 날짜·같은 분류(물가/고용/금리)면 예상치만 메모에 합침
        key_map = {(e["us_date"], e["cat"]): e for e in evs if e["src"] == "확정"}
        for f in ff:
            k = (f["us_date"], f["cat"])
            if f["cat"] in ("물가", "고용", "금리") and k in key_map:
                base = key_map[k]
                if f.get("forecast") and f["title"] not in base["note"]:
                    base["note"] = (base["note"] + " · " if base["note"] else "") + \
                        f"{f['title']} {f['note']}"
                continue
            evs.append(f)

    tk_set = {t.upper() for t in (holding_tickers or set())}
    today = today_kst()
    out = []
    seen = set()
    for e in evs:
        if not (today <= e["date"] <= end):
            continue
        k = (e["date"], e["title"])
        if k in seen:
            continue
        seen.add(k)
        e = dict(e)
        e["d_day"] = (e["date"] - today).days
        e["icon"] = CAT_ICON.get(e["cat"], "📌")
        # 내 포트폴리오 관련성
        rel = set(e["tags"])
        if tk_set & {"SOXL", "SOXX", "NVDA", "AMD", "MU", "AVGO", "TSM"} and "반도체" in rel:
            rel.add("내 자산 영향")
        if tk_set & {"QQQ", "QLD", "TQQQ", "MAGS", "IGV"} and "나스닥" in rel:
            rel.add("내 자산 영향")
        if tk_set & {"ETH", "BTC"} and "코인" in rel:
            rel.add("내 자산 영향")
        if e["cat"] in ("금리", "물가", "고용") and e["imp"] >= 3:
            rel.add("내 자산 영향")
        if e["ticker"] and e["ticker"] in tk_set:
            rel.add("내 자산 영향")
        e["mine"] = "내 자산 영향" in rel
        e["tags"] = [t for t in e["tags"] if t != "내 자산 영향"]
        out.append(e)
    return sorted(out, key=lambda e: (e["date"], e["time"] or "99:99", -e["imp"]))


_WD = ["월", "화", "수", "목", "금", "토", "일"]


def fmt_event_date(d: date) -> str:
    return f"{d.month}/{d.day}({_WD[d.weekday()]})"


def dday_label(n: int) -> str:
    return "오늘" if n == 0 else ("내일" if n == 1 else f"D-{n}")
