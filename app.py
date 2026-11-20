from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from core.calc import (
    aggregate_by_ticker,
    allocation_by_bucket,
    allocation_by_class,
    allocation_by_kind,
    allocation_invest_only,
    build_rows_from_raw,
    combine,
    concentration,
    currency_exposure,
    family_summary,
    leverage_ratio,
    load_fx_safe,
    pnl_contribution,
    rebalancing_signals,
    risk_metrics,
    summary,
    weighted_leverage,
)
from core.config import ASSET_CASH, ASSET_COIN, ASSET_STOCK, PORTFOLIO_XLSX, RAW_DIR
from core.datasource import get_dashboard_data, is_cloud
from core.loaders import load_family, load_meta
from core.market import (
    get_fear_and_greed,
    get_market_dashboard,
    get_market_regime,
    market_temperature,
    vix_color,
)
from core.news import get_market_news, news_sentiment
from core.bubble import compute_bubble_index
from core.raw_loader import account_history, load_raw, raw_info, raw_timestamp, split_holdings_and_cash
from core.history_extra import append_total
from core.snapshots import append_snapshot, load_snapshots
from core.tunnel import public_url, tunnel_log_mtime

st.set_page_config(page_title="내 포트폴리오", page_icon="📊", layout="wide")

# ── 모든 Plotly 차트에서 툴바(확대/이동/캡처 등) 숨기고 드래그 줌 비활성화 ──
_PLOTLY_CONFIG = {"displayModeBar": False, "scrollZoom": False,
                  "staticPlot": False, "doubleClick": False}
_orig_plotly_chart = st.plotly_chart


def _plotly_chart(fig, *args, **kwargs):
    kwargs.setdefault("config", _PLOTLY_CONFIG)
    try:
        fig.update_layout(dragmode=False)  # 드래그 줌/패닝 끔 (호버 툴팁은 유지)
    except Exception:
        pass
    return _orig_plotly_chart(fig, *args, **kwargs)


st.plotly_chart = _plotly_chart


# ──────────────────────────────────────────────────────────────────────
#  테마 / 디자인 시스템  (주간·야간 + 색감 프리셋/랜덤)
# ──────────────────────────────────────────────────────────────────────
import random as _random

# 추천 색감 프리셋 (accent, name)
ACCENT_PRESETS = [
    ("#4C78A8", "클래식 블루"), ("#2E7D32", "포레스트 그린"),
    ("#6C5CE7", "바이올렛"),    ("#E8590C", "선셋 오렌지"),
    ("#0CA678", "민트"),        ("#D6336C", "라즈베리"),
    ("#1098AD", "오션 틸"),     ("#7048E8", "인디고"),
    ("#F08C00", "앰버"),        ("#C2255C", "마젠타"),
]


def _hex_to_rgba(hx: str, a: float) -> str:
    hx = hx.lstrip("#")
    r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
    return f"rgba({r},{g},{b},{a})"


def inject_theme(mode: str, accent: str) -> None:
    """주간/야간 + accent 색을 CSS로 주입. 카드형 metric, 둥근 모서리, 폰트 개선."""
    if mode == "night":
        bg = "#0E1117"; bg2 = "#161B22"; card = "#1C2330"; text = "#F0F4F9"
        sub = "#C2CCD8"; border = "rgba(255,255,255,0.09)"
    else:
        bg = "#F7F8FA"; bg2 = "#FFFFFF"; card = "#FFFFFF"; text = "#1A1F2B"
        sub = "#4A5360"; border = "rgba(0,0,0,0.06)"
    acc = accent
    acc_soft = _hex_to_rgba(acc, 0.12)
    if mode == "night":
        pos, neg, warn = "#51CF66", "#FF6B6B", "#FFC24B"
    else:
        pos, neg, warn = "#2B8A3E", "#E03131", "#B26B00"

    st.markdown(f"""
    <style>
      :root {{
        --card: {card}; --text: {text}; --sub: {sub}; --border: {border};
        --acc: {acc}; --acc-soft: {acc_soft}; --pos: {pos}; --neg: {neg}; --warn: {warn};
      }}
      .stApp {{ background: {bg}; color: {text}; }}
      /* ── v3 디자인 요소 ── */
      .hero {{ background: linear-gradient(135deg, {_hex_to_rgba(acc, 0.17)}, {_hex_to_rgba(acc, 0.03)});
        border: 1px solid {_hex_to_rgba(acc, 0.30)}; border-radius: 18px; padding: 16px 20px 12px; margin: 2px 0 12px; }}
      .hero-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); gap: 12px 22px; }}
      .h-label {{ font-size: .8rem; font-weight: 700; color: var(--sub); }}
      .h-val {{ font-size: 2.0rem; font-weight: 850; color: var(--text); line-height: 1.15; letter-spacing: -.02em; }}
      .h-sub {{ font-size: .82rem; color: var(--sub); margin-top: 2px; }}
      .h-foot {{ margin-top: 11px; padding-top: 9px; border-top: 1px dashed var(--border); font-size: .78rem; color: var(--sub); }}
      .pos {{ color: var(--pos) !important; }}
      .neg {{ color: var(--neg) !important; }}
      .bcard {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 11px 14px; margin-bottom: 10px; }}
      .bcard.red {{ border-left: 5px solid #E03131; }}
      .bcard.yellow {{ border-left: 5px solid #F59F00; }}
      .bcard.green {{ border-left: 5px solid #2BA84A; }}
      .bcard.gray {{ border-left: 5px solid #868E96; }}
      .bcard.acc {{ border-left: 5px solid var(--acc); }}
      .bc-title {{ font-weight: 800; font-size: .95rem; color: var(--text); display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }}
      .bc-metric {{ font-weight: 800; white-space: nowrap; }}
      .bc-body {{ font-size: .84rem; color: var(--sub); margin-top: 4px; line-height: 1.55; }}
      .bc-body ul {{ margin: 4px 0 0 0; padding-left: 18px; }}
      .grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 0 10px; }}
      .grid-tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(122px, 1fr)); gap: 8px; margin: 6px 0 12px; }}
      .tile {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 8px 11px; }}
      .tile .t-l {{ font-size: .72rem; color: var(--sub); font-weight: 700; }}
      .tile .t-v {{ font-size: 1.12rem; font-weight: 850; color: var(--text); }}
      .tile .t-s {{ font-size: .7rem; color: var(--sub); }}
      .tile.me {{ border: 1.5px solid var(--acc); background: var(--acc-soft); }}
      .pill {{ display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: .7rem; font-weight: 800; margin-left: 4px; vertical-align: middle; white-space: nowrap; }}
      .pill.red {{ background: rgba(224,49,49,.14); color: var(--neg); }}
      .pill.yellow {{ background: rgba(245,159,0,.18); color: var(--warn); }}
      .pill.green {{ background: rgba(43,168,74,.15); color: var(--pos); }}
      .pill.gray {{ background: rgba(134,142,150,.16); color: var(--sub); }}
      .pill.acc {{ background: var(--acc-soft); color: var(--acc); }}
      ul.brief {{ list-style: none; padding-left: 0; margin: 4px 0 0 0; }}
      ul.brief li {{ margin: 5px 0; line-height: 1.55; font-size: .9rem; color: var(--text); }}
      .ev-week {{ font-weight: 800; color: var(--sub); font-size: .8rem; margin: 14px 0 2px; }}
      .ev-row {{ display: flex; gap: 12px; padding: 8px 2px; border-bottom: 1px solid var(--border); align-items: flex-start; }}
      .ev-d {{ min-width: 66px; text-align: center; }}
      .dday {{ font-weight: 850; font-size: .8rem; border-radius: 9px; padding: 3px 6px; background: var(--acc-soft); color: var(--acc); }}
      .dday.hot {{ background: rgba(224,49,49,.14); color: var(--neg); }}
      .ev-date {{ font-size: .74rem; color: var(--sub); margin-top: 3px; }}
      .ev-title {{ font-weight: 750; font-size: .9rem; color: var(--text); }}
      .ev-note {{ font-size: .78rem; color: var(--sub); margin-top: 2px; }}
      .stars {{ color: #F59F00; font-size: .72rem; letter-spacing: -1px; margin-left: 3px; }}
      .hl {{ font-size: .86rem; margin: 6px 0; line-height: 1.45; }}
      .hl a {{ color: var(--text) !important; text-decoration: none; font-weight: 650; }}
      .hl a:hover {{ color: var(--acc) !important; text-decoration: underline; }}
      .hl .src {{ color: var(--sub); font-size: .72rem; }}
      .sig-row {{ display: flex; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--border); align-items: baseline; }}
      .sig-row .nm {{ font-weight: 750; min-width: 180px; color: var(--text); }}
      .sig-row .vl {{ font-weight: 800; min-width: 96px; color: var(--text); }}
      .sig-row .mn {{ color: var(--sub); font-size: .8rem; }}
      table.mini {{ width: 100%; border-collapse: collapse; font-size: .82rem; margin-top: 6px; }}
      table.mini th {{ color: var(--sub); font-weight: 700; text-align: right; padding: 3px 6px; border-bottom: 1px solid var(--border); }}
      table.mini td {{ text-align: right; padding: 4px 6px; border-bottom: 1px solid var(--border); color: var(--text); }}
      table.mini th:first-child, table.mini td:first-child {{ text-align: left; }}
      div[data-testid="stSegmentedControl"] button {{ font-weight: 700; }}
      @media (max-width: 680px) {{
        .h-val {{ font-size: 1.55rem; }}
        .sig-row {{ flex-wrap: wrap; gap: 4px 10px; }}
        .sig-row .nm, .sig-row .vl {{ min-width: auto; }}
        .grid2 {{ grid-template-columns: 1fr; }}
      }}
      section[data-testid="stSidebar"] {{ background: {bg2}; border-right: 1px solid {border}; }}
      /* metric 카드화 */
      div[data-testid="stMetric"] {{
        background: {card}; border: 1px solid {border}; border-radius: 14px;
        padding: 14px 16px; box-shadow: 0 1px 3px {_hex_to_rgba('#000000', 0.05)};
        transition: transform .12s ease, box-shadow .12s ease;
      }}
      div[data-testid="stMetric"]:hover {{
        transform: translateY(-2px); box-shadow: 0 6px 18px {_hex_to_rgba(acc, 0.18)};
        border-color: {_hex_to_rgba(acc, 0.5)};
      }}
      div[data-testid="stMetricValue"] {{
        font-weight: 800; color: {text};
        font-size: 1.45rem; white-space: normal; overflow: visible;
        text-overflow: clip; line-height: 1.15;
      }}
      div[data-testid="stMetricValue"] > div {{ overflow: visible !important; text-overflow: clip !important; }}
      div[data-testid="stMetricLabel"] {{ overflow: visible; }}
      div[data-testid="stMetricLabel"] p {{
        color: {sub}; font-weight: 600; font-size: 0.78rem;
        white-space: normal; overflow: visible; text-overflow: clip;
      }}
      /* 좁은 화면(폰) — 글자 더 줄이고 카드 패딩 축소 */
      @media (max-width: 680px) {{
        div[data-testid="stMetricValue"] {{ font-size: 1.05rem; }}
        div[data-testid="stMetricLabel"] p {{ font-size: 0.66rem; }}
        div[data-testid="stMetric"] {{ padding: 8px 9px; border-radius: 11px; }}
      }}
      /* 탭 */
      button[data-baseweb="tab"] {{ font-weight: 700; }}
      div[data-baseweb="tab-highlight"] {{ background: {acc} !important; }}
      button[data-baseweb="tab"][aria-selected="true"] {{ color: {acc} !important; }}
      /* 제목/구분선 */
      h1, h2, h3 {{ color: {text}; letter-spacing: -.01em; }}
      hr {{ border-color: {border}; }}
      /* 표 */
      div[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; border: 1px solid {border}; }}
      /* 버튼 */
      .stButton button {{ border-radius: 10px; font-weight: 700; }}
      .stButton button:hover {{ border-color: {acc}; color: {acc}; }}
      /* 정보/경고 박스 살짝 */
      div[data-testid="stExpander"] {{ border-radius: 12px; border: 1px solid {border}; }}
      /* 좌측 하단 실시간 인디케이터 */
      #live-dot {{
        position: fixed; left: 14px; bottom: 12px; z-index: 9999;
        background: {card}; border: 1px solid {border}; border-radius: 999px;
        padding: 6px 12px; font-size: 12px; color: {sub};
        box-shadow: 0 2px 8px {_hex_to_rgba('#000000', 0.12)};
        display: flex; align-items: center; gap: 7px;
      }}
      #live-dot .dot {{
        width: 9px; height: 9px; border-radius: 50%; background: #2BD46A;
        box-shadow: 0 0 0 0 rgba(43,212,106,.7); animation: pulse 1.8s infinite;
      }}
      @keyframes pulse {{
        0% {{ box-shadow: 0 0 0 0 rgba(43,212,106,.6); }}
        70% {{ box-shadow: 0 0 0 9px rgba(43,212,106,0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(43,212,106,0); }}
      }}
      /* accent 강조 텍스트 */
      strong {{ color: {text}; }}
    </style>
    """, unsafe_allow_html=True)


def _now_kst() -> datetime:
    """한국시간 (클라우드 서버는 UTC 라서 보정)."""
    from core.events import KST
    return datetime.now(KST).replace(tzinfo=None)


def render_live_dot() -> None:
    """좌측 하단 실시간 갱신 인디케이터 (60초마다 자동 새로고침과 연동)."""
    now = _now_kst().strftime("%H:%M:%S")
    st.markdown(
        f'<div id="live-dot"><span class="dot"></span>실시간 · {now} 갱신</div>',
        unsafe_allow_html=True,
    )


def fmt_short(v: float) -> str:
    """원화 컴팩트 표기: 1.8억 / 3,450만 / 12,000 (막대 라벨용)."""
    v = float(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e8:
        return f"{sign}{a/1e8:.2f}억"
    if a >= 1e4:
        return f"{sign}{a/1e4:,.0f}만"
    return f"{sign}{a:,.0f}"


# 24시간 고정 클라우드 주소 (Streamlit Community Cloud)
CLOUD_APP_URL = "https://jeremykim96-portfolio-dashboard-app-fzmnk9.streamlit.app"


def _get_password() -> str:
    """비밀번호: Streamlit secrets(APP_PASSWORD) 우선, 없으면 1996."""
    try:
        pw = st.secrets.get("APP_PASSWORD")
        if pw:
            return str(pw)
    except Exception:
        pass
    return "1996"


PASSWORD = _get_password()


def gate_password() -> None:
    """비밀번호 게이트. 통과 못 하면 st.stop()."""
    if st.session_state.get("authed"):
        return
    st.title("🔒 내 포트폴리오")
    st.caption("비밀번호를 입력하세요.")
    with st.form("login", clear_on_submit=True):
        pwd = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("입장", width="stretch")
    if submitted:
        if pwd == PASSWORD:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("비밀번호가 틀립니다.")
    st.stop()


gate_password()

# 환율은 60초마다 자동 갱신 (실시간감)
st_autorefresh(interval=60_000, key="fx_refresh")


import re as _re


def _plain(label: str) -> str:
    """plotly 차트 제목용 — 앞쪽 이모지/기호 제거 (한글/영문/숫자부터 시작).

    plotly 기본 폰트가 이모지를 렌더 못 해 '□'로 깨지는 것 방지.
    """
    if not label:
        return ""
    return _re.sub(r"^[^가-힣A-Za-z0-9]+", "", str(label)).strip()


def fmt_krw(v: float) -> str:
    return f"{v:,.0f}원"


def fmt_pct(v: float) -> str:
    return f"{v * 100:+.2f}%"


def render_sidebar(raw_meta: dict, raw_ts, fx_err: str | None, usd_krw: float,
                   cloud: bool = False) -> None:
    with st.sidebar:
        if cloud:
            # 클라우드(폰 뷰): 읽기 전용 안내
            st.header("☁️ 클라우드 뷰")
            st.caption("PC에서 올린 데이터를 보는 화면입니다 (읽기 전용). "
                       "데이터 갱신은 PC에서 `push_to_cloud.bat` 실행 시 반영됩니다.")
            if raw_meta.get("mtime"):
                st.caption(f"마지막 업로드: {raw_meta['mtime']:%Y-%m-%d %H:%M}")
            if raw_ts:
                st.caption(f"RAW 기준일시: {raw_ts:%Y-%m-%d %H:%M}")
            st.divider()
            st.metric("USD / KRW", f"{usd_krw:,.2f}")
            if st.button("🔄 시장지표 새로고침", width="stretch"):
                st.cache_data.clear()
                st.rerun()
            if fx_err:
                st.error(fx_err)
            return

        # 로컬(PC): 외부 접속 링크 + 데이터 폴더
        st.header("📱 외부 접속")
        st.success(f"🌐 {CLOUD_APP_URL}")
        st.caption("**고정 주소** — 폰 홈화면에 추가하세요. PC 꺼져도 24시간 작동 (같은 비밀번호).")
        ext = public_url()
        if ext:
            with st.expander("보조 주소 (PC 켜져 있을 때만, 재부팅 시 변경)"):
                st.code(ext, language="text")

        st.divider()

        st.header("⚙️ 데이터")
        st.metric("USD / KRW", f"{usd_krw:,.2f}", help="60초마다 자동 갱신")

        if st.button("🔄 시세 새로고침", width="stretch"):
            st.cache_data.clear()
            st.rerun()

        st.caption("**RAW 데이터 폴더**")
        st.code(raw_meta.get("folder", "?"), language="text")
        if raw_meta.get("name"):
            st.success(f"📄 {raw_meta['name']}")
            if raw_meta.get("mtime"):
                st.caption(f"파일 수정: {raw_meta['mtime']:%Y-%m-%d %H:%M}")
            if raw_ts:
                st.caption(f"RAW 기준일시: {raw_ts:%Y-%m-%d %H:%M}")
        else:
            st.error("RAW 파일 없음 — 폴더에 자산 RAW DATA_*.xlsx 떨어뜨리세요")

        st.caption("**종목 메타 (선택)**")
        st.code(str(PORTFOLIO_XLSX), language="text")
        st.caption("meta 시트: 레버리지·자산버킷")

        if fx_err:
            st.error(fx_err)


def render_top_header(s: dict, fs: dict, fg: dict, vix: float | None,
                       regime: dict, temp: dict) -> None:
    """페이지 최상단 — 항상 보이는 핵심 4+4 KPI.

    1행: 운용 총자산 / 내 순자산 / 일간% / 주간%
    2행: F&G / VIX / S&P 국면 / 투자 컨디션
    """
    # 1행: 자산 KPI
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "🟩 운용자산",
        fmt_short(fs["total_managed"]),
        delta=f"가족 {fs['family_share']*100:.0f}%",
        delta_color="off",
        help=f"운용 총자산 = 내 순자산 + 가족 자산 − 대출 · 정확히 {fmt_krw(fs['total_managed'])}",
    )
    c2.metric(
        "🟦 내 순자산",
        fmt_short(s["total_eval"]),
        delta=f"{s['total_return']*100:+.1f}%",
        help=f"신한·업비트 RAW 기준 · 정확히 {fmt_krw(s['total_eval'])}",
    )
    c3.metric("📈 일간", fmt_pct(s["day_change"]))
    c4.metric("📈 주간", fmt_pct(s["week_change"]))

    # 2행: 시장 컨디션
    m1, m2, m3, m4 = st.columns(4)
    if fg:
        m1.metric(
            "😨 공포·탐욕",
            f"{fg['score']:.0f}",
            delta=_plain(fg.get("label", "")),
            delta_color="off",
            help=f"CNN Fear & Greed Index (0=극공포·100=극탐욕). {fg['advice']}",
        )
    else:
        m1.metric("😨 공포·탐욕", "—", help="API 응답 없음")

    if vix is not None:
        m2.metric(f"{vix_color(vix)} VIX",
                  f"{vix:,.1f}",
                  help="변동성지수. 20↑ 🟡 주의 · 30↑ 🔴 위험")
    else:
        m2.metric("VIX", "—")

    if regime:
        m3.metric("🏛️ S&P 국면", _plain(regime.get("status", "—")),
                  delta=f"50일선 {regime['vs_ma50_pct']*100:+.1f}%",
                  delta_color="off",
                  help=regime.get("status", ""))
    else:
        m3.metric("🏛️ S&P 국면", "—")

    m4.metric("🌡️ 컨디션", _plain(temp["label"]) if temp else "—",
              help=temp.get("advice", "") if temp else "")
    if temp and temp.get("advice"):
        st.caption(f"💡 **{temp['label']}** — {temp['advice']}")


def render_market_context() -> None:
    """상단 시장 패널 — VIX, 주요 지수, 환율 + 시장 국면 진단."""
    data = get_market_dashboard()
    if not data:
        return
    st.subheader("🌐 시장 컨텍스트")
    items = list(data.items())

    # 9~10개 카드를 2행으로 (한 줄 5개씩)
    per_row = 5
    for start in range(0, len(items), per_row):
        chunk = items[start:start + per_row]
        cols = st.columns(len(chunk))
        for col, (key, info) in zip(cols, chunk):
            price = info["price"]
            day = info["day_change"]
            label = info["label"]
            if key == "vix":
                label = f"{vix_color(price)} {label}"
            col.metric(
                label,
                info["fmt"].format(price),
                delta=f"{day*100:+.2f}%",
                help=f"심볼: {info['symbol']} · 주간 {info['week_change']*100:+.2f}%",
            )

    # (S&P 국면·투자 컨디션은 상단 헤더에 이미 표시 → 중복 제거)


def render_bubble(bubble: dict) -> None:
    """AI 버블 고점 판독기 — 종합 점수 게이지 + 구성요소별 근거."""
    st.subheader("🫧 AI 버블 고점 판독기")
    st.caption("시장·심리·기술·뉴스·내 포트폴리오를 종합한 휴리스틱 위험도 (0=정상, 100=고점위험). 투자자문 아님·참고용.")

    if not bubble or not bubble.get("components"):
        st.info("버블 지표 데이터를 불러오지 못했습니다.")
        return

    score = bubble["score"]
    left, right = st.columns([2, 3])

    with left:
        gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            number={"suffix": " / 100"},
            title={"text": _plain(bubble["verdict"]), "font": {"size": 18}},
            gauge={
                "axis": {"range": [0, 100], "tickvals": [0, 25, 45, 60, 75, 100]},
                "bar": {"color": "#222"},
                "steps": [
                    {"range": [0, 25],   "color": "#54A24B"},
                    {"range": [25, 45],  "color": "#A1D99B"},
                    {"range": [45, 60],  "color": "#FFE08A"},
                    {"range": [60, 75],  "color": "#FDAE61"},
                    {"range": [75, 100], "color": "#D7191C"},
                ],
            },
        ))
        gauge.update_layout(height=280, margin=dict(t=50, b=10, l=20, r=20))
        st.plotly_chart(gauge, width="stretch")

    with right:
        st.markdown(f"### {bubble['verdict']}  ·  **{score:.0f}점**")
        st.info(f"📌 **종합 판단**: {bubble['advice']}")
        # 구성요소 막대
        comp_df = pd.DataFrame([
            {"지표": c["name"], "점수": c["score"], "가중치": c["weight"]}
            for c in bubble["components"]
        ])
        fig = go.Figure(go.Bar(
            x=comp_df["점수"], y=comp_df["지표"], orientation="h",
            marker=dict(
                color=comp_df["점수"],
                colorscale=[[0, "#54A24B"], [0.45, "#FFE08A"], [0.6, "#FDAE61"], [1, "#D7191C"]],
                cmin=0, cmax=100,
            ),
            text=[f"{v:.0f}" for v in comp_df["점수"]],
            textposition="outside",
        ))
        fig.update_layout(height=240, xaxis=dict(range=[0, 110], title="과열도"),
                          yaxis=dict(autorange="reversed"),
                          margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch")

    # 구성요소별 상세 근거
    with st.expander("🔍 판독 근거 자세히 보기 (구성요소별 논리)", expanded=False):
        for c in bubble["components"]:
            st.markdown(f"**{c['name']}** — 과열도 `{c['score']:.0f}/100` "
                        f"· 가중치 {c['weight']*100:.0f}% · 현재값 `{c['value']}`")
            st.caption(c["reason"])
            st.divider()
        st.caption("⚠️ 이 지표는 공개 데이터 기반 규칙형 모델입니다. 미래 수익을 보장하지 않으며, "
                   "투자 결정의 보조 참고자료로만 사용하세요.")


def render_news(news_items: list) -> None:
    """미국 증시 영향 뉴스 top 10 — 제목 + 요약 + 출처 + 시간 + 링크."""
    st.subheader("📰 미국 증시 주요 뉴스 (실시간 Top 10)")
    if not news_items:
        st.info("뉴스를 불러오지 못했습니다. 잠시 후 새로고침 해보세요.")
        return

    sent = news_sentiment(news_items)
    s = sent["score"]
    mood = ("🟢 긍정 우세" if s > 0.2 else "🔴 부정 우세" if s < -0.2 else "⚪ 중립")
    st.caption(f"헤드라인 심리: **{mood}** (긍정 {sent['pos']} · 부정 {sent['neg']}) "
               "· 출처: MarketWatch · Yahoo · CNBC · Investing.com · 10분 갱신")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for i, it in enumerate(news_items, 1):
        pub = it.get("published")
        if pub:
            hrs = (now - pub).total_seconds() / 3600
            when = f"{hrs:.0f}시간 전" if hrs >= 1 else f"{(now-pub).total_seconds()/60:.0f}분 전"
        else:
            when = ""
        impact_badge = "🔥" * min(3, max(1, it.get("impact", 0) // 2))
        title_ko = it.get("title_ko") or it["title"]
        with st.container():
            # 한국어 제목(링크) 우선
            st.markdown(f"**{i}. [{title_ko}]({it['link']})**")
            meta_line = f"<small>{impact_badge} `{it['source']}`"
            if when:
                meta_line += f" · {when}"
            meta_line += "</small>"
            st.markdown(meta_line, unsafe_allow_html=True)
            # 한국어 요약
            if it.get("summary_ko"):
                st.caption(f"📝 {it['summary_ko']}")
            # 영문 원문 (접기)
            with st.expander("🔤 영문 원문", expanded=False):
                st.markdown(f"**{it['title']}**")
                if it.get("summary"):
                    st.caption(it["summary"])
        st.divider()


def render_fear_greed_detail(fg: dict) -> None:
    """CNN F&G Index 상세 — 현재 + 이전 종가/주/월/년 비교 + 게이지."""
    if not fg:
        return
    st.subheader("😨 CNN Fear & Greed Index")
    st.caption("0=극단공포 · 50=중립 · 100=극단탐욕. 출처: CNN production.dataviz")

    score = fg["score"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("현재", f"{score:.1f}", help=fg.get("advice", ""))
    if fg.get("prev_close") is not None:
        d = score - fg["prev_close"]
        c2.metric("어제", f"{fg['prev_close']:.1f}", delta=f"{d:+.1f}", delta_color="off")
    if fg.get("prev_week") is not None:
        d = score - fg["prev_week"]
        c3.metric("1주 전", f"{fg['prev_week']:.1f}", delta=f"{d:+.1f}", delta_color="off")
    if fg.get("prev_month") is not None:
        d = score - fg["prev_month"]
        c4.metric("1개월 전", f"{fg['prev_month']:.1f}", delta=f"{d:+.1f}", delta_color="off")
    if fg.get("prev_year") is not None:
        d = score - fg["prev_year"]
        c5.metric("1년 전", f"{fg['prev_year']:.1f}", delta=f"{d:+.1f}", delta_color="off")

    # 게이지 차트
    gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"suffix": " / 100"},
        title={"text": _plain(fg.get("label", "")), "font": {"size": 20}},
        gauge={
            "axis": {"range": [0, 100], "tickvals": [0, 25, 45, 55, 75, 100]},
            "bar": {"color": "#222"},
            "steps": [
                {"range": [0, 25],   "color": "#8B0000"},
                {"range": [25, 45],  "color": "#FF8C00"},
                {"range": [45, 55],  "color": "#BDBDBD"},
                {"range": [55, 75],  "color": "#74c476"},
                {"range": [75, 100], "color": "#006d2c"},
            ],
        },
    ))
    gauge.update_layout(height=240, margin=dict(t=40, b=20, l=20, r=20))
    st.plotly_chart(gauge, width="stretch")
    if fg.get("advice"):
        st.info(f"📌 **투자 시사**: {fg['advice']}")


def render_kpis(s: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("순자산 (부채차감)", fmt_short(s["total_eval"]),
              delta=f"총자산 {fmt_short(s['gross_assets'])}",
              delta_color="off",
              help=f"정확히 {fmt_krw(s['total_eval'])}")
    c2.metric("투자 수익률", fmt_pct(s["total_return"]),
              delta=fmt_short(s["total_pnl"]))
    c3.metric("일간 변동", fmt_pct(s["day_change"]))
    c4.metric("주간 변동", fmt_pct(s["week_change"]))

    if s["debt"] > 0:
        st.caption(f"💳 대출잔액 **{fmt_krw(s['debt'])}** 차감 반영됨.")


def render_concentration(invest_rows: pd.DataFrame) -> None:
    c = concentration(invest_rows)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("보유 종목 수", f"{c['n_holdings']}개")
    c2.metric("1위 종목 비중", f"{c['top1_share']*100:.1f}%")
    c3.metric("상위 3종목", f"{c['top3_share']*100:.1f}%")
    c4.metric("집중도(HHI)", f"{c['hhi']:.3f}",
              help="0.1↓ 분산 / 0.18↑ 집중. 종목 비중 제곱합")


def render_leverage_kpis(invest_rows: pd.DataFrame, s: dict) -> None:
    """레버리지 관련 4개 KPI."""
    wl = weighted_leverage(invest_rows)
    lr = leverage_ratio(s)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "가중 레버리지",
        f"{wl['weighted']:.2f}×",
        help="Σ(평가금액 × 레버리지) / Σ평가금액. 종목별 레버리지는 meta 시트 입력.",
    )
    c2.metric(
        "실효 시장 노출",
        fmt_krw(wl["effective_exposure"]),
        help="실제로 시장 가격 움직임에 노출된 자본 (레버리지 포함).",
    )
    c3.metric(
        "LTV (대출/총자산)",
        f"{lr['ltv']*100:.1f}%",
        help="총자산 대비 대출잔액 비율. 0% = 대출 없음.",
    )
    c4.metric(
        "총자산/순자산",
        f"{lr['gross_to_net']:.2f}×",
        help="대출로 부풀린 정도. 1.00 = 대출 없음, 1.10 = 순자산의 10% 추가 노출.",
    )


def _pie(df: pd.DataFrame, names_col: str, title: str, color_map: dict | None = None):
    if df.empty:
        st.info(f"({title}) 데이터 없음")
        return
    fig = px.pie(df, values="평가금액", names=names_col, hole=0.55,
                 color=names_col, color_discrete_map=color_map)
    fig.update_traces(textposition="inside", textinfo="percent+label",
                      hovertemplate="<b>%{label}</b><br>%{value:,.0f}원<br>%{percent}<extra></extra>")
    fig.update_layout(title=title, showlegend=False,
                      margin=dict(t=40, b=10, l=10, r=10), height=320)
    st.plotly_chart(fig, width="stretch")


def render_allocation_charts(all_rows: pd.DataFrame) -> None:
    st.subheader("비중 분석")

    class_colors = {"해외주식": "#4C78A8", "암호화폐": "#F58518", "현금": "#54A24B"}
    kind_colors = {"개별주": "#4C78A8", "ETF": "#72B7B2",
                   "코인": "#F58518", "현금": "#54A24B"}

    row1 = st.columns(2)
    with row1[0]:
        _pie(allocation_by_class(all_rows), "분류", "자산군 비중",
             color_map=class_colors)
    with row1[1]:
        _pie(allocation_invest_only(all_rows), "분류", "주식 vs 코인 (투자자산)",
             color_map=class_colors)

    row2 = st.columns(2)
    with row2[0]:
        _pie(allocation_by_kind(all_rows), "종류", "종류별 (ETF / 개별주 / 코인)",
             color_map=kind_colors)
    with row2[1]:
        _pie(allocation_by_bucket(all_rows), "자산버킷", "자산버킷 (성격별)")


def render_top_and_pnl(invest_rows: pd.DataFrame, accent: str = "#4C78A8") -> None:
    if invest_rows.empty:
        return
    left, right = st.columns(2)

    with left:
        st.subheader("상위 종목 TOP 5")
        top = invest_rows.nlargest(5, "평가금액")[["종목명", "평가금액"]].copy()
        # 큰 값일수록 진한 accent (색 대비)
        mx = float(top["평가금액"].max()) or 1.0
        shades = [_hex_to_rgba(accent, 0.45 + 0.55 * (v / mx)) for v in top["평가금액"]]
        fig = go.Figure(go.Bar(
            x=top["평가금액"], y=top["종목명"], orientation="h",
            marker_color=shades,
            text=[fmt_short(v) for v in top["평가금액"]],
            textposition="inside", insidetextanchor="end",
            textfont=dict(color="white", size=13, family="Arial Black"),
            hovertemplate="<b>%{y}</b><br>%{x:,.0f}원<extra></extra>",
        ))
        fig.update_layout(yaxis=dict(autorange="reversed"),
                          xaxis=dict(visible=False),
                          margin=dict(t=10, b=10, l=10, r=20), height=320,
                          uniformtext=dict(minsize=10, mode="hide"))
        st.plotly_chart(fig, width="stretch")

    with right:
        st.subheader("종목별 손익 기여")
        df = pnl_contribution(invest_rows)
        df["color"] = df["손익"].apply(lambda x: "#2BA84A" if x >= 0 else "#E03131")
        # 0 기준 양수는 막대 안 오른쪽, 음수는 안 왼쪽 → 잘림 방지
        positions = ["inside" if abs(v) > 0 else "outside" for v in df["손익"]]
        fig = go.Figure(go.Bar(
            x=df["손익"], y=df["종목명"], orientation="h",
            marker_color=df["color"],
            text=[fmt_short(v) if v >= 0 else fmt_short(v) for v in df["손익"]],
            textposition=positions,
            insidetextanchor="end",
            textfont=dict(color="white", size=12, family="Arial Black"),
            hovertemplate="<b>%{y}</b><br>%{x:+,.0f}원<extra></extra>",
        ))
        fig.update_layout(yaxis=dict(autorange="reversed"),
                          xaxis=dict(visible=False, zeroline=True,
                                     zerolinecolor="rgba(128,128,128,.4)"),
                          margin=dict(t=10, b=10, l=10, r=10), height=320,
                          uniformtext=dict(minsize=9, mode="hide"))
        st.plotly_chart(fig, width="stretch")


def render_family_panel(family_df: pd.DataFrame, my_net_eval: float) -> None:
    """가족 자산 + 운용 총자산 패널.

    내 투자자산 KPI 바로 옆에 따로 영역. 사용자가 한눈에 본인+가족 운용 규모 파악.
    """
    fs = family_summary(family_df, my_net_eval)

    st.subheader("👨‍👩‍👧 가족 자산 · 운용 총자산")
    st.caption("`portfolio.xlsx`의 **family** 시트에 항목별 금액 입력. "
               "**대출/부채**는 항목명에 '대출'을 넣으면 자동 차감됩니다 (금액은 양수로 입력).")

    # 상단 합계 KPI — 자산 / 대출 / 운용총자산 (컴팩트 억/만 표기)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟦 내 순자산 (RAW)", fmt_short(my_net_eval),
              help=f"RAW 기준 부채차감 순자산 · 정확히 {fmt_krw(my_net_eval)}")
    c2.metric("🟨 가족 자산", fmt_short(fs["family_assets"]),
              help=f"보증금·투자자산 등 (대출 제외) · 정확히 {fmt_krw(fs['family_assets'])}")
    if fs["family_debt"] > 0:
        c3.metric("🟥 대출 (부채)", f"-{fmt_short(fs['family_debt'])}",
                  help=f"'대출' 항목 합계. 차감 반영 · 정확히 {fmt_krw(fs['family_debt'])}")
    else:
        c3.metric("🟥 대출 (부채)", "없음",
                  help="family 시트에 '대출' 항목을 추가하면 여기 차감 표시됩니다.")
    c4.metric("🟩 운용 총자산", fmt_short(fs["total_managed"]),
              delta="내 + 가족 − 대출",
              delta_color="off",
              help=f"내 순자산 + 가족 자산 − 대출 · 정확히 {fmt_krw(fs['total_managed'])}")

    # 항목별 상세 + 도넛
    if fs["items"]:
        left, right = st.columns([3, 2])
        with left:
            rows = []
            for it in fs["items"]:
                is_debt = it.get("type") == "debt"
                share = (it["amount"] / fs["total_managed"]
                         if fs["total_managed"] and not is_debt else None)
                rows.append({
                    "구분": "🟥 대출" if is_debt else "🟦 자산",
                    "항목": it["label"],
                    "금액(KRW)": f"{it['amount']:,.0f}",
                    "비중(총자산)": f"{share*100:.2f}%" if share is not None else "—",
                    "메모": it["memo"],
                })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        with right:
            # 내 자산 + 가족 항목들을 한 도넛으로
            chart_rows = [{"구분": "내 순자산", "금액": float(my_net_eval)}]
            for it in fs["items"]:
                if it["amount"] != 0:
                    chart_rows.append({"구분": it["label"], "금액": float(it["amount"])})
            chart_df = pd.DataFrame(chart_rows)
            chart_df = chart_df[chart_df["금액"] > 0]
            if not chart_df.empty:
                fig = px.pie(chart_df, values="금액", names="구분", hole=0.55)
                fig.update_traces(textposition="inside", textinfo="percent+label",
                                  hovertemplate="<b>%{label}</b><br>%{value:,.0f}원<br>%{percent}<extra></extra>")
                fig.update_layout(title="운용 총자산 구성", showlegend=False,
                                  margin=dict(t=40, b=10, l=10, r=10), height=280)
                st.plotly_chart(fig, width="stretch")
    else:
        st.info("📝 `portfolio.xlsx` → **family** 시트에 항목별 금액을 채우면 표시됩니다.")


def render_currency_exposure(all_rows: pd.DataFrame, usd_krw: float) -> None:
    """통화 노출 분리 — USD 평가액, KRW 평가액, 환율 1원당 영향."""
    fx = currency_exposure(all_rows)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("USD 노출 (KRW 환산)", fmt_krw(fx["usd_krw"]),
              help="USD 평가금액. 환율이 1% 오르면 KRW 평가액도 1% 증가.")
    c2.metric("USD 비중", f"{fx['usd_share']*100:.1f}%")
    c3.metric("KRW 노출", fmt_krw(fx["krw"]),
              help="원화 표시 자산 (코인 원화시세, 현금).")
    # 환율 ±5% 시 평가 변동 시뮬레이션
    delta_5 = fx["usd_krw"] * 0.05
    c4.metric("환율 ±5% 영향", f"±{delta_5:,.0f}원",
              help=f"USD/KRW {usd_krw:,.2f} 기준. USD 노출액이 큰 만큼 환변동 민감.")


def render_risk_panel(snapshots_df: pd.DataFrame) -> None:
    """포트폴리오 리스크 지표 — 스냅샷 기반."""
    rm = risk_metrics(snapshots_df)
    if rm["n_days"] < 2:
        st.info("📊 스냅샷이 더 누적되면 변동성·MDD·Sharpe 등이 표시됩니다.")
        return

    st.subheader("📐 리스크 지표")
    if not rm.get("dense"):
        c1, c2, c3 = st.columns(3)
        c1.metric("최대 낙폭(MDD)", f"{rm['mdd']*100:.1f}%",
                  help="RAW 기록 기준 내 순자산의 최고점 대비 최대 하락폭 (입출금 포함).")
        c2.metric("기록 간 최대 상승", f"{rm['best_day']*100:+.1f}%")
        c3.metric("기록 간 최대 하락", f"{rm['worst_day']*100:+.1f}%")
        st.caption(f"RAW 기록 {rm.get('n_obs', 0)}개 · 평균 간격 {rm.get('avg_gap', 0):.0f}일 — "
                   "변동성·Sharpe 같은 '일별' 지표는 기록 간격이 넓어 계산하지 않습니다 "
                   "(RAW를 자주 올릴수록 정확해져요).")
        return
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("연환산 변동성", f"{rm['daily_vol_ann']*100:.1f}%",
              help="일별 수익률 표준편차 × √252. 20% 이상이면 변동 큰 편.")
    c2.metric("최대 낙폭(MDD)", f"{rm['mdd']*100:.1f}%",
              help="누적 최고점 대비 최대 하락폭. 0%에 가까울수록 안정.")
    c3.metric("Sharpe", f"{rm['sharpe']:.2f}",
              help="리스크 대비 초과수익 (무위험 3% 가정). 1↑ 양호 / 2↑ 우수.")
    c4.metric("Sortino", f"{rm['sortino']:.2f}",
              help="하방 변동성만 페널티. Sharpe보다 공격적 평가.")
    c5.metric("승률", f"{rm['win_rate']*100:.0f}%",
              help=f"양수일 비율 ({rm['n_days']}일 중)")
    c6.metric("최고/최저일", f"{rm['best_day']*100:+.2f}% / {rm['worst_day']*100:+.2f}%")


def render_rebalancing(invest_rows: pd.DataFrame, meta_df: pd.DataFrame) -> None:
    """리밸런싱 시그널 — 목표비중 vs 현재."""
    sig = rebalancing_signals(invest_rows, meta_df, threshold=0.05)
    if sig.empty:
        st.info("⚖️ `data/portfolio.xlsx`의 `meta` 시트에 **목표비중(%)** 컬럼을 채우면 리밸런싱 권장이 표시됩니다.")
        return

    st.caption("내가 meta 시트에 정한 목표비중과의 차이 계산값입니다 (±5%p 이상이면 이탈 표시). "
               "'목표까지 차액' = 목표비중에 맞추려면 늘거나 줄어야 하는 금액.")

    view = sig.copy()
    view["종목명"] = [t if str(n).isascii() else n for n, t in zip(view["종목명"], view["티커"])]
    view["현재비중"] = (view["현재비중"] * 100).map(lambda x: f"{x:.2f}%")
    view["목표비중"] = (view["목표비중"] * 100).map(lambda x: f"{x:.1f}%")
    view["차이(%p)"] = (view["차이(%p)"] * 100).map(lambda x: f"{x:+.2f}")
    view["필요액(KRW)"] = view["필요액(KRW)"].map(lambda x: f"{x:+,.0f}")
    view = view.rename(columns={"필요액(KRW)": "목표까지 차액(원)"})
    st.dataframe(view, width="stretch", hide_index=True)


def render_diagnostics(invest_rows: pd.DataFrame) -> None:
    """종목 진단 — 52주 위치, RSI, 이평선 대비."""
    if invest_rows.empty or "RSI" not in invest_rows.columns:
        return
    df = invest_rows[invest_rows["평가금액"] > 0].copy()
    if df.empty:
        return

    st.subheader("🩺 종목 진단 (매수 타이밍 보조)")
    st.caption("**RSI** 70↑ 과매수 · 30↓ 과매도 / **52주 위치** 1.0=최고가, 0.0=최저가 / **이평 대비** 양수면 상승추세")

    view = df[["종목명", "티커", "52주위치", "RSI", "MA50대비", "MA200대비", "변동성", "평가금액"]].copy()
    view = view.sort_values("평가금액", ascending=False)

    def rsi_emoji(v):
        if v >= 70: return f"🔴 {v:.0f}"
        if v >= 60: return f"🟡 {v:.0f}"
        if v <= 30: return f"🟢 {v:.0f}"
        if v <= 40: return f"🟢 {v:.0f}"
        return f"⚪ {v:.0f}"

    def pos_emoji(p):
        if p >= 0.9: return f"🔴 {p*100:.0f}%"
        if p <= 0.2: return f"🟢 {p*100:.0f}%"
        return f"⚪ {p*100:.0f}%"

    view["52주위치"] = view["52주위치"].map(pos_emoji)
    view["RSI"] = view["RSI"].map(rsi_emoji)
    view["MA50대비"] = view["MA50대비"].map(lambda x: f"{x*100:+.1f}%")
    view["MA200대비"] = view["MA200대비"].map(lambda x: f"{x*100:+.1f}%")
    view["변동성"] = view["변동성"].map(lambda x: f"{x*100:.0f}%" if x else "—")
    view["평가금액"] = view["평가금액"].map(lambda x: f"{x:,.0f}")
    st.dataframe(view, width="stretch", hide_index=True)


def _filter_period(df: pd.DataFrame, datecol: str, period: str) -> pd.DataFrame:
    """기간 필터. '최근 1개월'/'최근 3개월'/'최근 6개월'/'전체'."""
    if period == "전체":
        return df
    days = {"최근 1개월": 31, "최근 3개월": 93, "최근 6개월": 186}.get(period)
    if not days:
        return df
    last_date = pd.to_datetime(df[datecol]).max()
    cutoff = last_date - pd.Timedelta(days=days)
    sub = df[pd.to_datetime(df[datecol]) >= cutoff]
    return sub if len(sub) >= 2 else df.tail(max(2, len(df) // 3))


def _nearest_before(df: pd.DataFrame, datecol: str, valcol: str,
                    target: pd.Timestamp, tol_days: float) -> tuple[float, pd.Timestamp] | None:
    """target 에 가장 가까운(과거 우선) 행의 값. 허용오차 밖이면 None."""
    d = df.copy()
    d[datecol] = pd.to_datetime(d[datecol])
    d = d[d[datecol] < d[datecol].max()]  # 현재(마지막) 시점 제외
    if d.empty:
        return None
    d["dist"] = (d[datecol] - target).abs().dt.days
    best = d.nsmallest(1, "dist").iloc[0]
    if best["dist"] > tol_days:
        return None
    return float(best[valcol]), best[datecol]


def _period_chips(full: pd.DataFrame) -> None:
    """기간별 자산 변동 칩 — 1주/1개월/3개월/1년/최고점 대비 (자산앱 스타일)."""
    cur = float(full["total"].iloc[-1])
    last_date = pd.to_datetime(full["date"]).max()

    chips: list[tuple[str, str, str, str]] = []  # (label, value, delta, help)
    for days, name in [(7, "1주 전"), (30, "1개월 전"), (91, "3개월 전"), (365, "1년 전")]:
        hit = _nearest_before(full, "date", "total", last_date - pd.Timedelta(days=days),
                              tol_days=days * 0.75 + 4)
        if hit is None:
            chips.append((name, "—", "", "해당 시점 근처 데이터 없음 (누적되면 채워짐)"))
        else:
            base, refd = hit
            diff = cur - base
            pct = diff / base * 100 if base else 0
            chips.append((name, f"{diff/1e8:+.2f}억", f"{pct:+.1f}%",
                          f"기준 {refd:%Y-%m-%d}: {fmt_short(base)} → 현재 {fmt_short(cur)}"))

    # 최고점 대비 (드로다운)
    imax = full["total"].idxmax()
    peak = float(full.loc[imax, "total"])
    peak_d = pd.to_datetime(full.loc[imax, "date"])
    dd = (cur / peak - 1) * 100 if peak else 0
    chips.append(("🏔️ 최고점 대비", f"{dd:+.1f}%", f"{(cur-peak)/1e8:+.2f}억",
                  f"최고 {fmt_short(peak)} ({peak_d:%Y-%m-%d})"))

    cols = st.columns(len(chips))
    for col, (name, val, delta, hlp) in zip(cols, chips):
        col.metric(name, val, delta=delta if delta else None,
                   delta_color="off", help=hlp)


def _eok_ticks(lo: float, hi: float, n: int = 5) -> tuple[list[float], list[str]]:
    """y축 눈금을 '억' 단위로 (1,500,000,000 → 15억)."""
    span = max(hi - lo, 1.0)
    step = span / n
    for s_ in [1e6, 2e6, 5e6, 1e7, 2e7, 2.5e7, 5e7, 1e8, 2e8, 2.5e8, 5e8, 1e9, 2e9, 5e9]:
        if s_ >= step:
            step = s_
            break
    import math as _m
    v = _m.ceil(lo / step) * step
    vals, txt = [], []
    while v <= hi + 1e-6 and len(vals) < 20:
        vals.append(v)
        txt.append(f"{v/1e8:g}억" if abs(v) >= 1e8 or v == 0 else f"{v/1e4:,.0f}만")
        v += step
    return vals, txt


def _selective_label(vals: pd.Series) -> list[str]:
    """시작·최고·최저·현재 지점만 라벨 (전부 라벨 = 클러터/잘림 원인)."""
    n = len(vals)
    idx = {0, n - 1, int(vals.values.argmax()), int(vals.values.argmin())}
    return [fmt_short(v) if i in idx else "" for i, v in enumerate(vals)]


def render_total_trend(total_hist: pd.DataFrame, accent: str = "#4C78A8",
                       compact: bool = False) -> None:
    """총자산 추이 — 기간별 변동 칩 + 최고점 마커 + 월별 증감. 잘림 없는 라벨."""
    if total_hist is None or total_hist.empty or len(total_hist) < 2:
        st.info("총자산 추이 데이터가 아직 부족합니다.")
        return
    full = total_hist.sort_values("date").reset_index(drop=True).copy()

    # ── 헤드라인: 현재 총자산 + 직전 대비 ──
    last = full.iloc[-1]
    prev = full.iloc[-2]
    chg_abs = last["total"] - prev["total"]
    chg_pct = (chg_abs / prev["total"] * 100) if prev["total"] else 0
    first = full.iloc[0]
    growth = (last["total"] / first["total"] - 1) * 100 if first["total"] else 0
    k1, k2, k3 = st.columns([2, 1, 1])
    k1.metric("💰 현재 총자산", fmt_short(last["total"]),
              delta=f"{chg_abs/1e8:+.2f}억 ({chg_pct:+.1f}%) · 직전 기록 대비",
              help=f"정확히 {fmt_krw(last['total'])} · 직전 기록 {pd.to_datetime(prev['date']):%m/%d}")
    k2.metric("📈 시작 대비", f"{growth:+.0f}%",
              help=f"{fmt_short(first['total'])} ({pd.to_datetime(first['date']):%y.%m}) → 현재")
    k3.metric("🗓️ 기록", f"{len(full)}개 시점")

    # ── 기간별 변동 칩 (1주/1개월/3개월/1년/최고점) ──
    _period_chips(full)

    # ── 추이 / 월별 증감 탭 ──
    t_trend, t_monthly = st.tabs(["📈 추이", "📊 월별 증감"])

    with t_trend:
        period = st.radio("기간", ["전체", "최근 6개월", "최근 3개월", "최근 1개월"],
                          horizontal=True, index=0, key="tt_period",
                          label_visibility="collapsed")
        df = _filter_period(full, "date", period).reset_index(drop=True)
        xlabels = pd.to_datetime(df["date"]).dt.strftime("%y.%m.%d")

        fig = go.Figure(go.Scatter(
            x=xlabels, y=df["total"], mode="lines+markers+text",
            line=dict(color=accent, width=3, shape="spline"),
            marker=dict(size=7),
            fill="tozeroy", fillcolor=_hex_to_rgba(accent, 0.10),
            text=_selective_label(df["total"]),
            textposition="top center", textfont=dict(size=11),
            cliponaxis=False,  # 라벨이 축 경계에서 잘리지 않게
            hovertemplate="%{x}<br>총자산 %{y:,.0f}원<extra></extra>",
        ))
        # 최고점 마커
        imax = int(df["total"].values.argmax())
        fig.add_trace(go.Scatter(
            x=[xlabels.iloc[imax]], y=[df["total"].iloc[imax]],
            mode="markers", marker=dict(size=13, symbol="star", color="#F59F00"),
            hovertemplate="최고점 %{y:,.0f}원<extra></extra>", showlegend=False,
        ))
        ymin, ymax = float(df["total"].min()), float(df["total"].max())
        span = ymax - ymin
        pad = span * 0.22 if span > 0 else ymax * 0.05
        lo_y, hi_y = ymin - pad * 0.6, ymax + pad
        tv, tt = _eok_ticks(lo_y, hi_y)
        fig.update_layout(
            height=310 if compact else 370,
            yaxis=dict(range=[lo_y, hi_y], tickvals=tv, ticktext=tt),
            xaxis=dict(type="category", title=""),
            margin=dict(t=34, b=10, l=10, r=58),  # 오른쪽 여백 → 마지막 라벨 잘림 방지
            title_text="", showlegend=False,
        )
        st.plotly_chart(fig, width="stretch")
        st.caption(f"⭐ 최고점 {fmt_short(df['total'].max())} · "
                   f"라벨은 시작·최고·최저·현재만 표시 (나머지는 마우스 올리면 확인)")

    with t_monthly:
        m = full.copy()
        m["date"] = pd.to_datetime(m["date"])
        monthly = m.set_index("date")["total"].resample("ME").last().dropna()
        delta = monthly.diff().dropna()
        if delta.empty:
            st.info("월별 증감을 계산할 데이터가 부족합니다.")
        else:
            xm = delta.index.strftime("%y.%m")
            colors = ["#2BA84A" if v >= 0 else "#E03131" for v in delta.values]
            fig = go.Figure(go.Bar(
                x=xm, y=delta.values, marker_color=colors,
                text=[f"{v/1e8:+.2f}억" for v in delta.values],
                textposition="outside", cliponaxis=False,
                textfont=dict(size=11),
                hovertemplate="%{x}<br>%{y:+,.0f}원<extra></extra>",
            ))
            fig.update_layout(
                height=310 if compact else 360,
                yaxis=dict(tickformat=",.0f", range=[min(delta.min(), 0) * 1.35,
                                                     max(delta.max(), 0) * 1.35]),
                xaxis=dict(type="category"),
                margin=dict(t=30, b=10, l=10, r=30),
            )
            st.plotly_chart(fig, width="stretch")
            best_m = delta.idxmax()
            st.caption(f"💡 월말 기준 증감. 최고의 달: **{best_m:%Y년 %m월} "
                       f"({delta.max()/1e8:+.2f}억)**")


def render_account_history(acc_hist: pd.DataFrame) -> None:
    """계좌별 자산 증감 추이 — 엑셀 과거 + RAW 현재 통합."""
    st.subheader("🏦 계좌별 자산 추이")
    if acc_hist is None or acc_hist.empty:
        st.info("RAW 파일이 누적되면 계좌별 추이가 표시됩니다.")
        return

    n_ts = acc_hist["ts"].nunique()
    if n_ts < 2:
        st.info(f"📅 RAW 파일이 2개 이상 쌓이면 추이가 그려집니다. (현재 {n_ts}개 시점) "
                "RAW 폴더에 새 파일을 떨어뜨릴 때마다 한 점씩 추가됩니다.")
        return

    # 기간 선택
    period = st.radio("기간", ["전체", "최근 6개월", "최근 3개월", "최근 1개월"],
                      horizontal=True, index=0, key="acc_period",
                      label_visibility="collapsed")
    fdf = _filter_period(acc_hist, "ts", period)

    pivot = fdf.pivot_table(index="ts", columns="account",
                            values="eval_krw", aggfunc="last").sort_index()
    xcat = pd.to_datetime(pivot.index).strftime("%y.%m.%d")  # 균등 간격

    tab1, tab2 = st.tabs(["💰 평가액 (원)", "📊 증감률 (%, 첫 시점=0)"])

    account_colors = ["#4C78A8", "#F58518", "#54A24B", "#B279A2", "#9D755D",
                      "#72B7B2", "#E45756", "#EECA3B"]

    with tab1:
        fig = go.Figure()
        for i, acc in enumerate(pivot.columns):
            fig.add_trace(go.Scatter(
                x=xcat, y=pivot[acc], name=acc, mode="lines+markers",
                line=dict(width=2, color=account_colors[i % len(account_colors)]),
                connectgaps=True,
            ))
        fig.update_layout(hovermode="x unified", height=400,
                          yaxis_tickformat=",.0f", yaxis_title="평가액(원)",
                          xaxis=dict(type="category"),
                          margin=dict(t=20, b=20, l=10, r=45),
                          legend=dict(orientation="h", y=1.05))
        st.plotly_chart(fig, width="stretch")

    with tab2:
        norm = pivot.copy()
        for acc in norm.columns:
            base = norm[acc].dropna().iloc[0] if not norm[acc].dropna().empty else None
            norm[acc] = (norm[acc] / base - 1) * 100 if base else norm[acc]
        fig = go.Figure()
        for i, acc in enumerate(norm.columns):
            fig.add_trace(go.Scatter(
                x=xcat, y=norm[acc], name=acc, mode="lines+markers",
                line=dict(width=2, color=account_colors[i % len(account_colors)]),
                connectgaps=True,
            ))
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(hovermode="x unified", height=400,
                          yaxis_title="증감률(%)", yaxis_ticksuffix="%",
                          xaxis=dict(type="category"),
                          margin=dict(t=20, b=20, l=10, r=45),
                          legend=dict(orientation="h", y=1.05))
        st.plotly_chart(fig, width="stretch")

    # 계좌별 최신 변동 요약
    if len(pivot) >= 2:
        latest = pivot.iloc[-1]
        prev = pivot.iloc[-2]
        cols = st.columns(len(pivot.columns))
        for col, acc in zip(cols, pivot.columns):
            cur = latest[acc]
            pv = prev[acc]
            delta = (cur - pv) / pv * 100 if pv else 0.0
            col.metric(acc, fmt_krw(cur) if pd.notna(cur) else "—",
                       delta=f"{delta:+.2f}%")


def render_monthly_heatmap(snapshots_df: pd.DataFrame) -> None:
    """월×일 수익률 히트맵."""
    if snapshots_df is None or snapshots_df.empty or len(snapshots_df) < 5:
        return
    s = snapshots_df.sort_values("date").copy()
    s["date"] = pd.to_datetime(s["date"])
    s["ret"] = s["total_eval"].pct_change() * 100
    s = s.dropna(subset=["ret"])
    if s.empty:
        return
    s["month"] = s["date"].dt.strftime("%Y-%m")
    s["day"] = s["date"].dt.day

    pivot = s.pivot_table(index="month", columns="day", values="ret", aggfunc="last")
    if pivot.empty:
        return

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[f"{d}일" for d in pivot.columns],
        y=pivot.index,
        colorscale=[[0, "#E45756"], [0.5, "#F4F4F4"], [1, "#54A24B"]],
        zmid=0,
        text=[[f"{v:+.2f}%" if pd.notna(v) else "" for v in row] for row in pivot.values],
        texttemplate="%{text}",
        hovertemplate="<b>%{y}-%{x}</b><br>%{z:+.2f}%<extra></extra>",
        colorbar=dict(title="%"),
    ))
    fig.update_layout(
        title="월별 일일 수익률 히트맵",
        height=max(280, 50 * len(pivot.index) + 80),
        margin=dict(t=40, b=20, l=10, r=10),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch")


def render_change_heatmap(invest_rows: pd.DataFrame) -> None:
    """종목별 × 기간(일간/주간/월간/YTD) 등락률 히트맵.

    색상은 양수/음수 각각의 분포 90 percentile 기반으로 동적 매핑되어
    극단 값(예: SOXL +500%)에 의해 다른 종목 색이 흐려지지 않는다.
    """
    if invest_rows.empty:
        return
    periods = [c for c in ["일간", "주간", "월간", "YTD"] if c in invest_rows.columns]
    if not periods:
        return

    df = invest_rows[invest_rows["평가금액"] > 0].copy()
    if df.empty:
        return
    df = df.sort_values("평가금액", ascending=False)

    mat = (df[periods].astype(float) * 100).round(2)

    # 데이터 분포 기반 동적 zmin/zmax (90 percentile, 최소 ±3% 보장)
    arr = pd.Series(mat.values.flatten()).dropna()
    if not arr.empty:
        pos = arr[arr > 0]
        neg = arr[arr < 0]
        zmax = float(pos.quantile(0.90)) if not pos.empty else 5.0
        zmin = -float(neg.abs().quantile(0.90)) if not neg.empty else -5.0
        zmax = max(zmax, 3.0)
        zmin = min(zmin, -3.0)
    else:
        zmin, zmax = -5.0, 5.0

    # 디버전트 색상: 0%만 흰색, 양수는 즉시 옅은 초록부터 시작 → 진한 초록,
    # 음수는 즉시 옅은 빨강부터 시작 → 진한 빨강. 0 폭을 좁게 (0.49~0.51) 유지.
    colorscale = [
        [0.00, "#67000d"],   # 진한 빨강 (음수 끝)
        [0.10, "#a50f15"],
        [0.20, "#cb181d"],
        [0.30, "#ef3b2c"],
        [0.40, "#fb6a4a"],
        [0.49, "#fcae91"],   # 옅은 빨강 (0% 직전)
        [0.50, "#ffffff"],   # 흰색 (0%)
        [0.51, "#c7e9c0"],   # 옅은 초록 (0% 직후)
        [0.60, "#a1d99b"],
        [0.70, "#74c476"],
        [0.80, "#41ab5d"],
        [0.90, "#238b45"],
        [1.00, "#005a32"],   # 진한 초록 (양수 끝)
    ]

    fig = go.Figure(go.Heatmap(
        z=mat.values,
        x=periods,
        y=df["종목명"],
        text=[[f"{v:+.1f}%" for v in row] for row in mat.values],
        texttemplate="%{text}",
        colorscale=colorscale,
        zmin=zmin,
        zmax=zmax,
        zmid=0,
        hovertemplate="<b>%{y}</b> · %{x}<br>%{z:+.2f}%<extra></extra>",
        colorbar=dict(title="%", tickformat="+.0f"),
    ))
    fig.update_layout(
        title=f"종목별 등락률 (기간별) — 색상 범위: {zmin:+.0f}% ~ {zmax:+.0f}%",
        height=max(280, 28 * len(df) + 80),
        margin=dict(t=50, b=20, l=10, r=10),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch")


def render_timeseries(df: pd.DataFrame | None = None) -> None:
    st.subheader("시계열 추이")
    if df is None:
        df = load_snapshots()  # 클라우드는 번들의 snapshots 를 넘겨받음
    if df.empty or len(df) < 2:
        st.info("📅 데이터가 누적되면 추이 그래프가 표시됩니다. (앱을 실행할 때마다 하루 단위로 자동 저장됨)")
        if not df.empty:
            st.caption(f"현재 누적 스냅샷: {len(df)}건")
        return

    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 자산 추이", "💰 자산군별", "🆚 vs S&P500", "📉 기록별 증감률"
    ])

    accent = st.session_state.get("accent", "#4C78A8")
    # x를 균등 간격(카테고리)으로 → 날짜 공백 때문에 그래프가 늘어지는 문제 해결
    xlabels = pd.to_datetime(df["date"]).dt.strftime("%m/%d")

    with tab1:
        fig = go.Figure()
        # 총자산 — 채워진 영역 (메인). 라벨은 핵심 지점만 (잘림·클러터 방지)
        fig.add_trace(go.Scatter(
            x=xlabels, y=df["gross_assets"], name="총자산",
            mode="lines+markers+text",
            line=dict(color=accent, width=2.5),
            marker=dict(size=7),
            fill="tozeroy", fillcolor=_hex_to_rgba(accent, 0.12),
            text=_selective_label(df["gross_assets"]),
            textposition="top center", textfont=dict(size=11),
            cliponaxis=False,
            hovertemplate="%{x}<br>총자산 %{y:,.0f}원<extra></extra>",
        ))
        # 순자산(부채차감)이 총자산과 다를 때만 별도 라인
        if (df["debt"].abs() > 1).any():
            fig.add_trace(go.Scatter(
                x=xlabels, y=df["total_eval"], name="순자산(부채차감)",
                mode="lines+markers", line=dict(color="#E03131", width=2, dash="dot"),
                marker=dict(size=6),
                hovertemplate="%{x}<br>순자산 %{y:,.0f}원<extra></extra>",
            ))
        # y축: 데이터 범위로 확대 (변동성 보이게)
        ymin = float(df[["gross_assets", "total_eval"]].min().min())
        ymax = float(df[["gross_assets", "total_eval"]].max().max())
        pad = max((ymax - ymin) * 0.18, ymax * 0.02)
        fig.update_layout(
            hovermode="x unified", height=400,
            yaxis=dict(range=[ymin - pad, ymax + pad], tickformat=",.0f", title="원"),
            xaxis=dict(type="category", title=None),
            margin=dict(t=30, b=20, l=10, r=58),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, width="stretch")
        latest = df.iloc[-1]
        first = df.iloc[0]
        chg = (latest["gross_assets"] - first["gross_assets"]) / first["gross_assets"] * 100 if first["gross_assets"] else 0
        st.caption(f"📌 기간 변화: **{fmt_short(first['gross_assets'])} → {fmt_short(latest['gross_assets'])}** ({chg:+.1f}%) · {len(df)}개 시점")

    with tab2:
        d2 = df.copy()
        d2["x"] = xlabels.values
        long = d2.melt(id_vars=["x"],
                       value_vars=["stock_eval", "coin_eval", "cash_eval"],
                       var_name="자산군", value_name="평가금액")
        long["자산군"] = long["자산군"].map({
            "stock_eval": "해외주식", "coin_eval": "암호화폐", "cash_eval": "현금"
        })
        fig = px.area(long, x="x", y="평가금액", color="자산군",
                      color_discrete_map={"해외주식": "#4C78A8",
                                          "암호화폐": "#F58518",
                                          "현금": "#54A24B"})
        fig.update_layout(hovermode="x unified", height=380,
                          yaxis_tickformat=",.0f", xaxis=dict(type="category", title=None),
                          margin=dict(t=20, b=20, l=10, r=10))
        st.plotly_chart(fig, width="stretch")

    with tab3:
        # 같은 % 축에서 내 포폴 vs S&P500 비교 (이중축 제거 — 환율은 사이드바 참조)
        fig = go.Figure()
        base = df["total_eval"].iloc[0]
        my_norm = (df["total_eval"] / base - 1) * 100 if base else df["total_return"] * 100

        fig.add_trace(go.Scatter(x=xlabels, y=my_norm,
                                 name="내 포폴",
                                 mode="lines+markers",
                                 line=dict(color="#2BA84A", width=2.5)))

        spx_norm = _spx_normalized(df["date"])
        if spx_norm is not None:
            fig.add_trace(go.Scatter(x=xlabels, y=spx_norm,
                                     name="S&P500",
                                     mode="lines",
                                     line=dict(color="#4C78A8", width=1.5, dash="dash")))

        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(
            hovermode="x unified", height=380,
            yaxis=dict(title="누적 수익률(%)", ticksuffix="%"),
            xaxis=dict(type="category"),
            margin=dict(t=30, b=20, l=10, r=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, width="stretch")
        if spx_norm is not None and len(my_norm) > 0:
            gap = float(my_norm.iloc[-1]) - float(spx_norm[-1])
            verdict = "시장을 이기고 있음 🎉" if gap > 0 else "시장 대비 뒤처짐"
            st.caption(f"📌 같은 기간 S&P500 대비 **{gap:+.1f}%p** — {verdict}")

    with tab4:
        d = df.copy()
        d["daily_pct"] = d["total_eval"].pct_change() * 100
        d = d.dropna(subset=["daily_pct"])
        if d.empty:
            st.info("스냅샷이 더 누적되면 일별 증감률이 표시됩니다.")
            return
        d["x"] = pd.to_datetime(d["date"]).dt.strftime("%m/%d")
        colors = ["#E03131" if v < 0 else "#2BA84A" for v in d["daily_pct"]]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=d["x"], y=d["daily_pct"],
            marker_color=colors,
            name="일별 변동(%)",
            text=[f"{v:+.1f}%" for v in d["daily_pct"]],
            textposition="outside", textfont=dict(size=10),
            cliponaxis=False,
            hovertemplate="%{x}<br>%{y:+.2f}%<extra></extra>",
        ))
        if len(d) >= 7:
            d["ma7"] = d["daily_pct"].rolling(7, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=d["x"], y=d["ma7"], name="7일 이평",
                line=dict(color="#9D755D", width=2),
            ))
        fig.update_layout(hovermode="x unified", height=380,
                          yaxis_title="일별 변동률(%)", xaxis=dict(type="category"),
                          margin=dict(t=34, b=20, l=10, r=40))
        st.plotly_chart(fig, width="stretch")


def _spx_normalized(dates: pd.Series) -> pd.Series | None:
    """스냅샷 날짜 구간의 S&P500을 첫날 = 0%로 정규화. 실패 시 None."""
    try:
        import yfinance as yf  # 로컬 import — 실패 시 조용히 스킵
        start = pd.to_datetime(dates.iloc[0]).date()
        end = pd.to_datetime(dates.iloc[-1]).date()
        spx = yf.Ticker("^GSPC").history(start=start, end=end + pd.Timedelta(days=2),
                                         auto_adjust=False)["Close"].dropna()
        if spx.empty:
            return None
        base = float(spx.iloc[0])
        if base <= 0:
            return None
        # 스냅샷 날짜에 가장 가까운 SPX 종가로 정렬
        spx_norm = (spx / base - 1) * 100
        # 스냅샷 날짜 인덱스에 매칭 (forward-fill)
        target = pd.to_datetime(dates).dt.tz_localize(None)
        spx_norm.index = spx_norm.index.tz_localize(None)
        aligned = spx_norm.reindex(target, method="ffill")
        return aligned.values
    except Exception:
        return None


def _format_table(df: pd.DataFrame, show_account: bool = True,
                  show_kind: bool = True) -> pd.DataFrame:
    view = df.copy()
    for c in ["수익률", "일간", "주간", "월간", "YTD"]:
        if c in view.columns:
            view[c] = view[c].map(lambda x: f"{x*100:+.2f}%")
    for c in ["평가금액", "매수금액"]:
        if c in view.columns:
            view[c] = view[c].map(lambda x: f"{x:,.0f}")
    if "손익" in view.columns:
        view["손익"] = view["손익"].map(lambda x: f"{x:+,.0f}")
    for c in ["평단가", "현재가"]:
        if c in view.columns:
            view[c] = view[c].map(lambda x: f"{x:,.0f}")
    if "레버리지" in view.columns:
        view["레버리지"] = view["레버리지"].map(lambda x: f"{x:.1f}×" if x else "")

    cols = ["분류"]
    if show_kind and "종류" in view.columns:
        cols.append("종류")
    if show_account and "계좌" in view.columns:
        cols.append("계좌")
    cols += ["종목명", "티커", "자산버킷", "수량", "평단가", "현재가", "통화",
             "평가금액", "매수금액", "손익", "수익률",
             "일간", "주간", "월간", "YTD", "레버리지"]
    cols = [c for c in cols if c in view.columns]
    return view[cols]


def render_table(stock_rows: pd.DataFrame, coin_rows: pd.DataFrame,
                 cash_rows: pd.DataFrame) -> None:
    st.subheader("종목별 상세")
    accounts = sorted(stock_rows["계좌"].unique().tolist()) if not stock_rows.empty else []

    tabs = st.tabs(["📊 합산", "🇺🇸 해외주식 (계좌별)", "🪙 암호화폐", "💵 현금/부채"])

    with tabs[0]:
        agg_stock = aggregate_by_ticker(stock_rows) if not stock_rows.empty else stock_rows
        combined = combine(agg_stock, coin_rows, cash_rows)
        if combined.empty:
            st.info("내역 없음")
        else:
            st.dataframe(_format_table(combined, show_account=False),
                         width="stretch", hide_index=True)

    with tabs[1]:
        if stock_rows.empty:
            st.info("해외주식 보유내역이 없습니다.")
        else:
            account_filter = st.radio("계좌", ["전체"] + accounts,
                                      horizontal=True, key="acc_filter")
            df = stock_rows if account_filter == "전체" \
                else stock_rows[stock_rows["계좌"] == account_filter]
            st.dataframe(_format_table(df, show_account=True),
                         width="stretch", hide_index=True)

    with tabs[2]:
        if coin_rows.empty:
            st.info("암호화폐 보유내역이 없습니다.")
        else:
            st.dataframe(_format_table(coin_rows, show_account=False),
                         width="stretch", hide_index=True)

    with tabs[3]:
        if cash_rows.empty:
            st.info("현금/부채 내역이 없습니다.")
        else:
            st.dataframe(_format_table(cash_rows, show_account=False),
                         width="stretch", hide_index=True)
            st.caption("💡 대출잔액은 음수(-)로 입력. 자동으로 부채에 잡힙니다.")


# ──────────────────────────────────────────────────────────────────────
#  v3 대개편 — 히어로 · 브리핑 · 방향성 진단 · 왜 움직였나 · 일정 · 전망
# ──────────────────────────────────────────────────────────────────────
import html as _html

from core.calc import _weighted_change
from core.events import (dday_label, economic_calendar_this_week, fmt_event_date,
                         upcoming_events)
from core.insights import briefing, direction_checks, stress_test
from core.market_story import (fmt_level, fmt_move, korean_headlines, period_moves,
                               story_summary, theme_breakdown)
from core.outlook import (CLAUDE_NOTE, analog_studies, board_verdict, market_signals,
                          scenarios, signal_board)

_LVL_DOT = {"red": "🔴", "yellow": "🟡", "green": "🟢", "gray": "⚪"}


def esc(t) -> str:
    """HTML 이스케이프 + 마크다운 오작동 문자($ 수식, ~ 취소선) 무력화."""
    s = _html.escape(str("" if t is None else t), quote=True)
    return s.replace("$", "&#36;").replace("~", "&#126;")


def _md_bold(t: str) -> str:
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(t))


def _html_block(s: str) -> None:
    # 줄바꿈·들여쓰기 없는 한 줄 HTML (마크다운 코드블록 오인 방지)
    st.markdown(s, unsafe_allow_html=True)


def _cls(v: float) -> str:
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def _eok(v: float) -> str:
    """부호 있는 억/만 표기."""
    v = float(v or 0)
    a = abs(v)
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    if a >= 1e8:
        return f"{sign}{a/1e8:.2f}억"
    if a >= 1e4:
        return f"{sign}{a/1e4:,.0f}만"
    return f"{sign}{a:,.0f}"


def _short_name(name: str, ticker: str) -> str:
    name = str(name or "")
    return name if (name and len(name) <= 8 and not name.isascii()) else (ticker or name[:10])


def _diff_dates(diff: dict) -> tuple[str, str]:
    try:
        p = datetime.fromisoformat(diff["prev_ts"]).strftime("%m/%d")
        c = datetime.fromisoformat(diff["cur_ts"]).strftime("%m/%d")
        return p, c
    except Exception:
        return "직전", "최신"


# ---------- 업로드 상태 배너 ----------
def render_sync_banner(cloud: bool, data: dict, sync: dict | None) -> None:
    if cloud:
        gen = data.get("generated_at")
        if gen is not None:
            age = (_now_kst() - gen).days
            if age >= 3:
                st.warning(f"☁️ 이 화면의 자산 데이터는 **{gen:%m/%d} 업로드분 ({age}일 전)** 입니다. "
                           "최신 RAW를 보려면 PC에서 「자산 클라우드 올리기」를 실행하세요. "
                           "(시장 지표·뉴스·일정은 실시간)")
        return
    if not sync or not sync.get("pending"):
        return
    c1, c2 = st.columns([4, 1.4])
    with c1:
        when = ""
        if sync.get("pushed_at"):
            try:
                when = f" · 마지막 업로드 {datetime.fromisoformat(sync['pushed_at']):%m/%d %H:%M}"
            except ValueError:
                pass
        st.warning(f"📤 새 RAW **{sync['latest']}** 가 아직 폰(클라우드)에 올라가지 않았어요{when}")
    with c2:
        if st.button("☁️ 지금 폰으로 올리기", width="stretch", type="primary",
                     key="push_now"):
            with st.spinner("암호화 후 업로드 중… (10~30초)"):
                try:
                    from push_to_cloud import push
                    ok, msg = push(log=lambda *_: None)
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, f"오류: {e}"
            (st.success if ok else st.error)(msg)


# ---------- 히어로 ----------
def render_hero(s: dict, fs: dict, diff: dict | None, raw_ts, cloud: bool, gen_at) -> None:
    ret = float(s.get("total_return", 0) or 0)
    pnl = float(s.get("total_pnl", 0) or 0)
    blocks = [
        '<div><div class="h-label">🟩 운용 총자산 · 내+가족−대출</div>'
        f'<div class="h-val">{esc(fmt_short(fs["total_managed"]))}</div>'
        f'<div class="h-sub">가족 순자산 {esc(fmt_short(fs["family_total"]))} 포함 · '
        f'내 비중 {fs["my_share"]*100:.0f}%</div></div>',
        '<div><div class="h-label">🟦 내 투자자산 (순자산)</div>'
        f'<div class="h-val">{esc(fmt_short(s["total_eval"]))}</div>'
        f'<div class="h-sub">수익률 <b class="{_cls(ret)}">{ret*100:+.1f}%</b> · '
        f'평가손익 <b class="{_cls(pnl)}">{esc(_eok(pnl))}</b></div></div>',
    ]
    if diff:
        d = float(diff.get("delta", 0))
        p, c = _diff_dates(diff)
        pct = d / diff["prev_total"] * 100 if diff.get("prev_total") else 0
        blocks.append(
            f'<div><div class="h-label">📄 지난 RAW 대비 ({p} → {c})</div>'
            f'<div class="h-val {_cls(d)}">{esc(_eok(d))} <span style="font-size:1rem">({pct:+.1f}%)</span></div>'
            f'<div class="h-sub">시장 변동 <b class="{_cls(diff["market_effect"])}">{esc(_eok(diff["market_effect"]))}</b>'
            f' · 매매·입출금 {esc(_eok(diff["flow_effect"]))}</div></div>')
    dc, wc = float(s.get("day_change", 0) or 0), float(s.get("week_change", 0) or 0)
    foot = [f'오늘 <b class="{_cls(dc)}">{dc*100:+.2f}%</b>',
            f'최근 1주 <b class="{_cls(wc)}">{wc*100:+.2f}%</b>']
    if raw_ts:
        foot.append(f"📄 RAW 기준 {raw_ts:%Y-%m-%d %H:%M}")
    if cloud and gen_at is not None:
        foot.append(f"☁️ 업로드 {gen_at:%m/%d %H:%M}")
    _html_block(f'<div class="hero"><div class="hero-grid">{"".join(blocks)}</div>'
                f'<div class="h-foot">{" · ".join(foot)}</div></div>')


def render_market_strip(fg: dict, vix: float | None, regime: dict, temp: dict) -> None:
    m1, m2, m3, m4 = st.columns(4)
    if fg:
        m1.metric("😨 공포·탐욕", f"{fg['score']:.0f}", delta=_plain(fg.get("label", "")),
                  delta_color="off", help="CNN Fear & Greed Index (0=극공포·100=극탐욕)")
    else:
        m1.metric("😨 공포·탐욕", "—", help="API 응답 없음")
    if vix is not None:
        m2.metric(f"{vix_color(vix)} VIX", f"{vix:,.1f}", help="변동성지수. 20↑ 🟡 주의 · 30↑ 🔴 공포")
    else:
        m2.metric("VIX", "—")
    if regime:
        m3.metric("🏛️ S&P 국면", _plain(regime.get("status", "—")),
                  delta=f"50일선 {regime['vs_ma50_pct']*100:+.1f}%", delta_color="off")
    else:
        m3.metric("🏛️ S&P 국면", "—")
    m4.metric("🌡️ 컨디션", _plain(temp["label"]) if temp else "—",
              help=temp.get("advice", "") if temp else "")


def render_briefing(items: list[tuple[str, str]]) -> None:
    if not items:
        return
    lis = "".join(f"<li>{icon} {text}</li>" for icon, text in items)
    _html_block(f'<div class="bcard acc"><div class="bc-title"><span>🗞️ 오늘의 브리핑</span>'
                f'<span class="pill gray">{_now_kst():%m/%d %H:%M} 기준</span></div>'
                f'<ul class="brief">{lis}</ul></div>')


# ---------- 방향성 진단 ----------
def _card_html(c: dict) -> str:
    return (f'<div class="bcard {c["level"]}"><div class="bc-title">'
            f'<span>{_LVL_DOT.get(c["level"], "")} {esc(c["title"])}</span>'
            f'<span class="bc-metric">{esc(c.get("metric", ""))}</span></div>'
            f'<div class="bc-body">{esc(c["body"])}</div></div>')


def render_direction(diag: dict, compact: bool = False) -> None:
    title, desc = diag["stance"]
    _html_block(f'<div class="bcard acc"><div class="bc-title"><span>🧭 종합 방향성: {esc(title)}</span>'
                f'<span><span class="pill red">위험 {diag["reds"]}</span>'
                f'<span class="pill yellow">주의 {diag["yellows"]}</span></span></div>'
                f'<div class="bc-body">{esc(desc)}</div></div>')
    cards = diag["cards"]
    if compact:
        cards = [c for c in cards if c["level"] in ("red", "yellow")][:4] or cards[:2]
    _html_block('<div class="grid2">' + "".join(_card_html(c) for c in cards) + "</div>")


def render_raw_waterfall(diff: dict | None, accent: str, compact: bool = False) -> None:
    if not diff:
        st.info("RAW 파일이 2개 이상 쌓이면 '직전 RAW 대비 무엇이 바뀌었는지'가 표시됩니다. "
                "(클라우드는 다음 업로드부터 표시)")
        return
    p, c = _diff_dates(diff)
    movers = sorted([t for t in diff["tickers"] if abs(t["price_effect"]) >= 1],
                    key=lambda t: abs(t["price_effect"]), reverse=True)
    top = movers[:6 if compact else 9]
    rest = sum(t["price_effect"] for t in movers[len(top):])
    labels = [f"{p} 계좌 합계"] + [_short_name(t["name"], t["ticker"]) for t in top]
    values = [diff["prev_total"]] + [t["price_effect"] for t in top]
    measures = ["absolute"] + ["relative"] * len(top)
    if abs(rest) >= 1:
        labels.append("기타 종목")
        values.append(rest)
        measures.append("relative")
    labels += ["매매·입출금", f"{c} 계좌 합계"]
    values += [diff["flow_effect"], 0]
    measures += ["relative", "total"]
    text = [fmt_short(diff["prev_total"])] + [_eok(v) for v in values[1:-1]] + [fmt_short(diff["cur_total"])]

    cum, lo, hi = 0.0, None, None
    for v, m in zip(values, measures):
        cum = v if m == "absolute" else (cum if m == "total" else cum + v)
        lo = cum if lo is None else min(lo, cum)
        hi = cum if hi is None else max(hi, cum)
    span = max(hi - lo, abs(hi) * 0.05, 1.0)
    fig = go.Figure(go.Waterfall(
        orientation="h", y=labels, x=values, measure=measures, text=text,
        textposition="outside", cliponaxis=False,
        increasing=dict(marker=dict(color="#2BA84A")),
        decreasing=dict(marker=dict(color="#E03131")),
        totals=dict(marker=dict(color=accent)),
        connector=dict(line=dict(color="rgba(128,128,128,.35)", width=1)),
        customdata=[f"{v:+,.0f}원" if m == "relative" else f"{fmt_krw(diff['cur_total'] if m == 'total' else v)}"
                    for v, m in zip(values, measures)],
        hovertemplate="%{y}<br>%{customdata}<extra></extra>",
    ))
    fig.update_layout(
        height=(300 if compact else 380) + 12 * len(labels),
        yaxis=dict(autorange="reversed", type="category"),
        xaxis=dict(range=[lo - span * 0.45, hi + span * 0.35], tickformat=",.0f", showgrid=False,
                   visible=False),
        margin=dict(l=10, r=70, t=10, b=10), showlegend=False,
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(f"🟥 하락 · 🟩 상승 = 가격 변동으로 인한 평가액 변화 (직전 수량 기준). "
               f"'매매·입출금' = 수량 변화·예수금 증감·계좌 추가/제외 · 기간 {diff.get('days') or '?'}일")


def render_raw_change_tables(diff: dict | None) -> None:
    if not diff:
        return
    acc = pd.DataFrame(diff.get("accounts", []))
    if not acc.empty:
        st.markdown("**🏦 계좌별 변화**")
        view = pd.DataFrame({
            "계좌": acc["account"],
            "직전": acc["prev"].map(lambda v: fmt_short(v) if pd.notna(v) else "—"),
            "최신": acc["cur"].map(lambda v: fmt_short(v) if pd.notna(v) else "—"),
            "변화": acc["delta"].map(_eok),
            "상태": acc["status"],
        })
        st.dataframe(view, width="stretch", hide_index=True)
    tk = pd.DataFrame(diff.get("tickers", []))
    if not tk.empty:
        tk = tk[(tk["prev_eval"].abs() >= 10_000) | (tk["cur_eval"].abs() >= 10_000)]  # 먼지 잔량 제외
    if not tk.empty:
        st.markdown("**📋 종목별 변화 (가격효과 vs 매매효과)**")
        tk = tk.sort_values("price_effect")
        view = pd.DataFrame({
            "종목": [_short_name(n, t) for n, t in zip(tk["name"], tk["ticker"])],
            "상태": tk["status"],
            "수량": [f"{a:,.4g} → {b:,.4g}" for a, b in zip(tk["prev_qty"], tk["cur_qty"])],
            "가격 변화": tk["price_chg"].map(lambda v: f"{v*100:+.1f}%" if pd.notna(v) else "—"),
            "가격효과": tk["price_effect"].map(_eok),
            "매매효과": tk["qty_effect"].map(_eok),
            "현재 평가": tk["cur_eval"].map(fmt_short),
        })
        st.dataframe(view, width="stretch", hide_index=True)


def render_stress(stress: dict, net: float) -> None:
    st.subheader("🧪 시나리오 스트레스 테스트")
    st.caption("시장이 이렇게 움직이면 내 투자자산은? — 종목별 민감도(레버리지·베타) 기반 추정치")
    if not stress:
        st.info("투자자산이 없습니다.")
        return
    names = list(stress.keys())
    vals = [stress[n]["delta"] for n in names]
    fig = go.Figure(go.Bar(
        x=vals, y=names, orientation="h",
        marker_color=["#2BA84A" if v >= 0 else "#E03131" for v in vals],
        text=[f"{_eok(v)} ({stress[n]['pct']*100:+.1f}%)" for n, v in zip(names, vals)],
        textposition="outside", cliponaxis=False,
        customdata=[f"{v:+,.0f}원 · {stress[n]['desc']}" for n, v in zip(names, vals)],
        hovertemplate="%{y}<br>%{customdata}<extra></extra>",
    ))
    m = max(abs(v) for v in vals) or 1
    fig.update_layout(height=60 + 42 * len(names), yaxis=dict(autorange="reversed"),
                      xaxis=dict(range=[-m * 1.55, m * 1.55], visible=False),
                      margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")
    pick = st.selectbox("시나리오별 종목 영향 보기", names, index=names.index("복합 하락")
                        if "복합 하락" in names else 0, key="stress_pick")
    det = pd.DataFrame(stress[pick]["detail"])
    if not det.empty:
        st.caption(f"📌 {stress[pick]['desc']}")
        det = det[det["delta"].abs() >= 1]
        st.dataframe(pd.DataFrame({
            "종목": [_short_name(n, t) for n, t in zip(det["name"], det["ticker"])],
            "예상 변화": det["delta"].map(_eok),
        }), width="stretch", hide_index=True)
    st.caption("⚠️ 레버리지 ETF는 기간 중 등락 경로에 따라 실제 결과가 추정과 크게 다를 수 있습니다.")


def render_exposure(diag: dict, s: dict) -> None:
    ex = diag.get("exposure", {})
    exp, ratio = ex.get("exp", {}), ex.get("ratio", {})
    tiles = [("🎯 총 실효 노출", f"{diag.get('eff_ratio', 0):.2f}×",
              f"{sum(exp.values())/1e8:.2f}억 / 순자산 {fmt_short(s['total_eval'])}", True)]
    for k, nm in [("NDX", "나스닥 노출"), ("SEMI", "반도체 노출"), ("CRYPTO", "코인 노출")]:
        tiles.append((nm, f"{ratio.get(k, 0):.2f}×", f"{exp.get(k, 0)/1e8:.2f}억", False))
    tiles.append(("레버리지 ETF 비중", f"{diag.get('lev_share', 0)*100:.0f}%", "투자자산 대비", False))
    tiles.append(("가중 레버리지", f"{diag.get('weighted_lev', 0):.2f}×", "평가액 가중 평균 배수", False))
    tiles.append(("현금 비중", f"{diag.get('cash_share', 0)*100:.1f}%", "투자계좌 내", False))
    _html_block('<div class="grid-tiles">' + "".join(
        f'<div class="tile{" me" if me else ""}"><div class="t-l">{esc(a)}</div>'
        f'<div class="t-v">{esc(b)}</div><div class="t-s">{esc(c)}</div></div>'
        for a, b, c, me in tiles) + "</div>")
    st.caption("실효 노출 = 평가액 × 레버리지·민감도. 1.0× 초과면 순자산보다 큰 금액이 시장에 노출된 상태")


def render_rebalance_chart(rebal: pd.DataFrame, accent: str) -> None:
    if rebal is None or rebal.empty:
        st.info("⚖️ portfolio.xlsx의 meta 시트에 목표비중(%)을 채우면 현재 vs 목표 비교가 표시됩니다.")
        return
    d = rebal[rebal["목표비중"] > 0].copy()
    if d.empty:
        return
    d = d.sort_values("현재비중", ascending=True)
    names = [_short_name(n, t) for n, t in zip(d["종목명"], d["티커"])]
    fig = go.Figure()
    for y, a, b in zip(names, d["현재비중"] * 100, d["목표비중"] * 100):
        fig.add_trace(go.Scatter(x=[a, b], y=[y, y], mode="lines",
                                 line=dict(color="rgba(128,128,128,.45)", width=3),
                                 hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=d["목표비중"] * 100, y=names, mode="markers", name="목표",
                             marker=dict(symbol="diamond", size=11, color="#ADB5BD"),
                             hovertemplate="%{y} 목표 %{x:.1f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=d["현재비중"] * 100, y=names, mode="markers+text", name="현재",
                             marker=dict(size=12, color=accent),
                             text=[f"{v:.0f}%" for v in d["현재비중"] * 100],
                             textposition="middle right", cliponaxis=False,
                             hovertemplate="%{y} 현재 %{x:.1f}%<extra></extra>"))
    fig.update_layout(height=70 + 30 * len(d), xaxis=dict(ticksuffix="%", title=None),
                      yaxis=dict(type="category"),
                      margin=dict(l=10, r=40, t=30, b=10),
                      legend=dict(orientation="h", y=1.08, x=1, xanchor="right"))
    st.plotly_chart(fig, width="stretch")
    st.caption("● 현재 비중 · ◆ 목표 비중 (meta 시트). 선이 길수록 목표와 멀어진 종목")


# ---------- 왜 움직였나 ----------
_INVERSE = {"^VIX", "^TNX", "DX-Y.NYB"}
_NEUTRAL = {"KRW=X", "GC=F", "CL=F"}


def render_why_moved(s: dict, invest_rows: pd.DataFrame) -> None:
    st.subheader("🔍 왜 움직였나 — 시장 등락과 원인 이슈")
    per = st.segmented_control("기간", ["오늘", "이번주", "이번달"], default="이번주",
                               key="why_period", label_visibility="collapsed") or "이번주"
    with st.spinner("시장 데이터·뉴스 분석 중…"):
        moves = period_moves(per)
        heads = korean_headlines(per)
        themes = theme_breakdown(heads)

    my = {"오늘": float(s.get("day_change", 0) or 0), "이번주": float(s.get("week_change", 0) or 0)}
    try:
        my["이번달"] = _weighted_change(invest_rows, "월간") if not invest_rows.empty else 0.0
    except Exception:
        my["이번달"] = 0.0
    mv = my.get(per, 0.0)
    my_lab = {"오늘": "최근 1일", "이번주": "최근 1주", "이번달": "최근 1개월"}[per]

    tiles = [f'<div class="tile me"><div class="t-l">💼 내 투자자산 ({my_lab})</div>'
             f'<div class="t-v {_cls(mv)}">{mv*100:+.1f}%</div>'
             f'<div class="t-s">약 {esc(_eok(float(s.get("total_eval", 0)) * mv))}</div></div>']
    for m in moves:
        val = m["pct"] if m["unit"] == "pct" else m["diff"]
        cls = "" if m["symbol"] in _NEUTRAL else (_cls(-val) if m["symbol"] in _INVERSE else _cls(val))
        tiles.append(f'<div class="tile"><div class="t-l">{esc(m["label"])}</div>'
                     f'<div class="t-v {cls}">{esc(fmt_move(m))}</div>'
                     f'<div class="t-s">{esc(fmt_level(m))} · {m["base_date"]}→{m["last_date"]}</div></div>')
    _html_block('<div class="grid-tiles">' + "".join(tiles) + "</div>")

    if moves:
        st.info("📌 " + story_summary(per, moves, themes))

    if not themes:
        st.caption("뉴스 헤드라인을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return
    now = datetime.now().astimezone()
    cards = []
    for t in themes:
        tone_cls = "red" if t["tone"] == "하락 우세" else ("green" if t["tone"] == "상승 우세" else "gray")
        hls = []
        for h in t["items"]:
            ago = ""
            if h.get("published"):
                try:
                    hrs = (now - datetime.fromisoformat(h["published"])).total_seconds() / 3600
                    ago = f"{hrs:.0f}시간 전" if hrs < 48 else f"{hrs/24:.0f}일 전"
                except Exception:
                    pass
            hls.append(f'<div class="hl"><a href="{esc(h["link"])}" target="_blank">{esc(h["title"])}</a> '
                       f'<span class="src">{esc(h.get("source", ""))} · {ago}</span></div>')
        cards.append(f'<div class="bcard"><div class="bc-title"><span>{esc(t["label"])}</span>'
                     f'<span><span class="pill gray">{t["count"]}건</span>'
                     f'<span class="pill {tone_cls}">{esc(t["tone"])}</span></span></div>'
                     f'{"".join(hls)}</div>')
    st.markdown(f"**🧩 원인 테마 TOP {len(themes)}** — 한국어 헤드라인 {len(heads)}건 자동 분류")
    _html_block('<div class="grid2">' + "".join(cards) + "</div>")
    st.caption("출처: 구글뉴스(국내 언론) · 키워드 기반 자동 분류라 일부 부정확할 수 있음 · 30분 갱신")


# ---------- 일정 ----------
def _event_row(e: dict) -> str:
    hot = " hot" if e["d_day"] <= 3 and e["imp"] >= 3 else ""
    stars = "★" * e["imp"] + "☆" * (3 - e["imp"])
    pills = ""
    if e.get("mine"):
        pills += '<span class="pill acc">내 자산 영향</span>'
    for t in e.get("tags", [])[:3]:
        if t != "금리":
            pills += f'<span class="pill gray">{esc(t)}</span>'
    when = f"{e['time']} KST" if e.get("time") else "시간 미정"
    note = f" · {esc(e['note'])}" if e.get("note") else ""
    return (f'<div class="ev-row"><div class="ev-d"><div class="dday{hot}">{dday_label(e["d_day"])}</div>'
            f'<div class="ev-date">{fmt_event_date(e["date"])}</div></div>'
            f'<div><div class="ev-title">{e["icon"]} {esc(e["title"])}<span class="stars">{stars}</span>{pills}</div>'
            f'<div class="ev-note">{esc(when)}{note}</div></div></div>')


def render_events_section(events: list[dict]) -> None:
    st.subheader("📅 다가오는 핵심 일정 — 미국·반도체·코인")
    c1, c2 = st.columns([3, 2])
    with c1:
        view = st.segmented_control("보기", ["⭐ 핵심만", "🎯 내 자산 영향", "전체"],
                                    default="⭐ 핵심만", key="ev_view",
                                    label_visibility="collapsed") or "⭐ 핵심만"
    with c2:
        days = st.segmented_control("기간", ["2주", "1개월", "2개월"], default="1개월",
                                    key="ev_days", label_visibility="collapsed") or "1개월"
    lim = {"2주": 14, "1개월": 31, "2개월": 62}[days]
    evs = [e for e in events if e["d_day"] <= lim]
    if view.startswith("⭐"):
        evs = [e for e in evs if e["imp"] >= 3 or (e["imp"] >= 2 and e.get("mine"))]
    elif view.startswith("🎯"):
        evs = [e for e in evs if e.get("mine")]
    if not evs:
        st.info("해당 조건의 일정이 없습니다.")
        return
    nxt = next((e for e in evs if e["imp"] >= 3), evs[0])
    st.success(f"⏭️ 다음 핵심 이벤트: **{fmt_event_date(nxt['date'])} {nxt['title']}** "
               f"({dday_label(nxt['d_day'])}{', ' + nxt['time'] + ' KST' if nxt.get('time') else ''})")
    from core.events import today_kst
    from datetime import timedelta as _td
    t0 = today_kst()
    wk0 = t0 - _td(days=t0.weekday())
    html_parts, cur_wk = [], None
    for e in evs:
        wk = (e["date"] - wk0).days // 7
        if wk != cur_wk:
            cur_wk = wk
            ws = wk0 + _td(days=7 * wk)
            lab = "이번주" if wk == 0 else ("다음주" if wk == 1 else f"{wk}주 뒤")
            html_parts.append(f'<div class="ev-week">{lab} · {ws.month}/{ws.day} – '
                              f'{(ws + _td(days=6)).month}/{(ws + _td(days=6)).day}</div>')
        html_parts.append(_event_row(e))
    _html_block('<div class="bcard">' + "".join(html_parts) + "</div>")
    st.caption("★★★ 시장 전체를 흔드는 일정 · 시각은 한국시간(KST) · 실적일은 미국 날짜 기준(장전/장후 미정) · "
               "출처: 연준·BLS 공식 일정, ForexFactory, Yahoo Finance")


def render_econ_week() -> None:
    ff = economic_calendar_this_week()
    if not ff:
        return
    with st.expander(f"📊 이번주 미국 경제지표 결과·예상치 ({len(ff)}건)", expanded=False):
        rows = [{"날짜": fmt_event_date(e["date"]), "시각(KST)": e["time"],
                 "지표": e["title"], "중요": "★" * e["imp"],
                 "실제": e.get("actual") or "—", "예상": e.get("forecast") or "—",
                 "이전": e.get("previous") or "—"} for e in ff]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption("실제치가 예상보다 '물가는 높게 / 고용은 강하게' 나오면 금리 인상 우려 ↑ (기술주·코인에 부담)")


# ---------- 전망 ----------
def render_outlook(sig: dict, fg: dict, events: list[dict], stress: dict) -> None:
    st.subheader("🔮 앞으로 — 데이터가 가리키는 방향")
    st.caption("예측이 아니라 ① 현재 신호 ② 과거 비슷한 상황 이후 실제 결과 ③ 시나리오별 내 자산 영향을 보여줍니다.")
    rows = signal_board(sig, fg)
    if rows:
        verdict, sub = board_verdict(rows)
        body = "".join(f'<div class="sig-row"><span>{r["state"]}</span><span class="nm">{esc(r["name"])}</span>'
                       f'<span class="vl">{esc(r["value"])}</span><span class="mn">{esc(r["meaning"])}</span></div>'
                       for r in rows)
        _html_block(f'<div class="bcard acc"><div class="bc-title"><span>📡 시장 신호판: {esc(verdict)}</span>'
                    f'<span class="pill gray">{esc(sub)}</span></div>{body}</div>')
    else:
        st.info("시장 신호 데이터를 불러오지 못했습니다.")

    st.markdown("##### 📚 과거 비슷한 상황 이후엔 어떻게 됐나")
    with st.spinner("과거 수십 년 데이터 분석 중… (최초 1회)"):
        studies = analog_studies(sig) if sig else []
    if studies:
        cards = []
        for stu in studies:
            pill = '<span class="pill red">지금 해당</span>' if stu["active"] else '<span class="pill gray">참고</span>'
            trs = "".join(
                f'<tr><td>{lab}</td><td class="{_cls(v["median"])}">{v["median"]*100:+.1f}%</td>'
                f'<td>{v["win"]*100:.0f}%</td><td class="neg">{v["worst"]*100:+.0f}%</td>'
                f'<td class="pos">{v["best"]*100:+.0f}%</td></tr>'
                for lab, v in stu["stats"].items())
            cards.append(
                f'<div class="bcard {"red" if stu["active"] else "gray"}"><div class="bc-title">'
                f'<span>{esc(stu["name"])}</span>{pill}</div>'
                f'<div class="bc-body">{esc(stu["desc"])} · 과거 {stu["n"]}회 · {esc(stu["now"])}<br>'
                f'최근 사례: {esc(", ".join(stu["recent"]))}</div>'
                f'<table class="mini"><tr><th>이후</th><th>중간값</th><th>상승확률</th><th>최악</th><th>최고</th></tr>'
                f'{trs}</table></div>')
        _html_block('<div class="grid2">' + "".join(cards) + "</div>")
        st.caption("에피소드 = 조건을 처음 충족한 날(60거래일 내 중복 제외) · 수익률은 기초지수 기준 "
                   "(SOXL 같은 3배 ETF는 대략 3배 + 변동성 손실) · 과거가 미래를 보장하지 않음")
    else:
        st.info("과거 데이터 분석을 불러오지 못했습니다.")

    st.markdown("##### 🧭 시나리오 3가지 — 이런 일이 생기면")
    scs = scenarios(sig or {}, events, stress)
    cards = []
    for sc in scs:
        imp = sc.get("impact") or {}
        imp_txt = (f'내 투자자산 영향(추정): <b class="{_cls(imp.get("delta", 0))}">{esc(_eok(imp.get("delta", 0)))} '
                   f'({imp.get("pct", 0)*100:+.1f}%)</b>') if imp else ""
        lvl = "green" if sc["name"].startswith("🟢") else ("red" if sc["name"].startswith("🔴") else "yellow")
        lis = "".join(f"<li>{esc(t)}</li>" for t in sc["triggers"])
        cards.append(f'<div class="bcard {lvl}"><div class="bc-title"><span>{esc(sc["name"])}</span></div>'
                     f'<div class="bc-body"><ul>{lis}</ul>👀 체크: {esc(sc["watch"])}<br>{imp_txt}</div></div>')
    _html_block('<div class="grid2">' + "".join(cards) + "</div>")


def render_claude_note() -> None:
    n = CLAUDE_NOTE
    pts = "".join(f"<li>{_md_bold(p)}</li>" for p in n["points"])
    ahead = "".join(f"<li>{_md_bold(p)}</li>" for p in n["ahead"])
    srcs = " · ".join(f'<a href="{esc(u)}" target="_blank">{esc(t)}</a>' for t, u in n["sources"])
    _html_block(f'<div class="bcard acc"><div class="bc-title"><span>📝 Claude 시장 노트 — {esc(n["title"])}</span>'
                f'<span class="pill gray">작성 {esc(n["date"])}</span></div>'
                f'<div class="bc-body"><b>무엇이 시장을 흔들었나</b><ul>{pts}</ul>'
                f'<b>앞으로 볼 것</b><ul>{ahead}</ul>'
                f'<span class="hl"><span class="src">출처: {srcs}</span></span><br>'
                f'<i>작성 시점 이후 상황은 위의 자동 뉴스·신호판이 실시간 반영합니다. 투자 자문 아님.</i></div></div>')


def _theme_controls() -> tuple[str, str]:
    """사이드바 테마 선택 → (mode, accent). session_state 보존."""
    if "theme_mode" not in st.session_state:
        st.session_state.theme_mode = "light"
    if "accent" not in st.session_state:
        st.session_state.accent = ACCENT_PRESETS[0][0]

    with st.sidebar:
        st.markdown("### 🎨 화면 테마")
        mode_label = st.radio("모드", ["☀️ 주간", "🌙 야간"],
                              horizontal=True,
                              index=0 if st.session_state.theme_mode == "light" else 1,
                              label_visibility="collapsed")
        st.session_state.theme_mode = "light" if "주간" in mode_label else "night"

        names = [n for _, n in ACCENT_PRESETS]
        cur_name = next((n for a, n in ACCENT_PRESETS if a == st.session_state.accent), names[0])
        picked = st.selectbox("색감", names, index=names.index(cur_name))
        st.session_state.accent = next(a for a, n in ACCENT_PRESETS if n == picked)

        if st.button("🎲 색감 랜덤 추천", width="stretch"):
            st.session_state.accent = _random.choice(ACCENT_PRESETS)[0]
            st.rerun()
        st.divider()

    return st.session_state.theme_mode, st.session_state.accent


def main() -> None:
    # 테마 먼저 적용
    mode, accent = _theme_controls()
    inject_theme(mode, accent)

    st.markdown(f"## 📊 내 포트폴리오 <span class='pill gray'>{_now_kst():%m/%d %H:%M} 조회</span>",
                unsafe_allow_html=True)

    # ---------- 데이터 로드 (로컬 RAW 또는 클라우드 JSON) ----------
    usd_krw, fx_err = load_fx_safe()
    cloud = is_cloud()
    with st.spinner("데이터 불러오는 중…"):
        data = get_dashboard_data()

    raw_meta = data.get("raw_meta", {"name": None, "folder": "?", "mtime": None})
    raw_ts = data.get("raw_ts")

    if data.get("empty"):
        render_sidebar(raw_meta, raw_ts, fx_err, usd_krw, cloud)
        if cloud:
            st.warning("☁️ 아직 PC에서 업로드된 데이터가 없습니다. PC에서 `push_to_cloud.bat`을 한 번 실행해 주세요.")
        else:
            st.error("📁 RAW 파일이 없습니다.")
            st.info(
                f"**{raw_meta.get('folder')}** 폴더에 `자산 RAW DATA_*.xlsx` 파일을 떨어뜨려 주세요.\n\n"
                "RAW 파일 형식: 16개 컬럼 (기준일시 / 계좌번호 / 계좌별칭 / 계좌유형 / 구분 / 항목 / "
                "티커 / 보유수량 / 매수평균가(원) / 현재가(원) / 매수금액(원) / 평가금액(원) / "
                "평가손익(원) / 수익률(%) / 통화 / 비고)"
            )
        st.stop()

    render_sidebar(raw_meta, raw_ts, fx_err, usd_krw, cloud)

    stock_rows = data["stock_rows"]
    coin_rows = data["coin_rows"]
    cash_rows = data["cash_rows"]
    meta_df = data["meta_df"]
    family_df = data["family_df"]
    acc_hist = data["account_hist"]

    agg_stock = aggregate_by_ticker(stock_rows) if not stock_rows.empty else stock_rows
    all_rows_agg = combine(agg_stock, coin_rows, cash_rows)
    s = summary(all_rows_agg)
    invest_rows = all_rows_agg[all_rows_agg["분류"].isin([ASSET_STOCK, ASSET_COIN])]

    # 시장 데이터 (헤더용 — 가벼운 것만 먼저). 뉴스·버블은 시장 탭에서 계산.
    market_data = get_market_dashboard()
    vix_price = market_data.get("vix", {}).get("price") if market_data else None
    regime = get_market_regime("^GSPC")
    temp = market_temperature(vix_price, regime.get("status", "")) if regime else {}
    fg = get_fear_and_greed()

    fs = family_summary(family_df, float(s.get("total_eval", 0)))

    lev = weighted_leverage(invest_rows)
    lr = leverage_ratio(s)

    # 스냅샷 + 총자산 추이 자동 저장 (로컬에서만 — 클라우드는 읽기 전용)
    if not cloud:
        try:
            append_snapshot(s, all_rows_agg, usd_krw,
                            leverage=lev, ratio=lr, vix=vix_price)
            append_total(float(fs.get("total_managed", 0)))  # 운용 총자산 추이 누적
        except Exception as e:
            st.sidebar.warning(f"추이 저장 실패: {e}")

    snapshots_df = data.get("snapshots")
    if snapshots_df is None:
        snapshots_df = load_snapshots()
    total_hist = data.get("total_history")
    if total_hist is None:
        from core.history_extra import load_total_history as _lth
        total_hist = _lth()

    # ---------- 공통 계산 ----------
    diff = data.get("raw_diff")
    sync = None
    if not cloud:
        try:
            from core.datasource import cloud_sync_status
            sync = cloud_sync_status()
        except Exception:
            sync = None
    holding_tk = set(invest_rows["티커"].astype(str).str.upper()) if not invest_rows.empty else set()
    net = float(s.get("total_eval", 0) or 0)
    rebal = rebalancing_signals(invest_rows, meta_df, threshold=0.05)
    stress = stress_test(invest_rows, net)

    def _diag(sig: dict | None, events: list[dict]) -> dict:
        return direction_checks(s=s, invest_rows=invest_rows, all_rows=all_rows_agg,
                                total_hist=total_hist, vix=vix_price, fg=fg, sig=sig,
                                events=events, rebal=rebal, stress=stress)

    # ---------- 상단 (항상 표시): 업로드 알림 · 히어로 · 시장 컨디션 ----------
    render_sync_banner(cloud, data, sync)
    render_hero(s, fs, diff, raw_ts, cloud, data.get("generated_at"))
    render_market_strip(fg, vix_price, regime, temp)

    # ---------- 메뉴 (선택한 화면만 계산 → 빠름) ----------
    NAV = ["🏠 홈", "🧭 내 자산 진단", "📰 시장·이슈", "📅 일정·전망", "🔍 종목", "📈 추이·보유"]
    sel = st.segmented_control("메뉴", NAV, default=NAV[0], key="nav",
                               label_visibility="collapsed")
    if sel is None:
        sel = st.session_state.get("nav_last", NAV[0])
    st.session_state["nav_last"] = sel

    # ===== 🏠 홈 =====
    if sel == NAV[0]:
        events_fast = upcoming_events(45, holding_tk, include_network=False)
        diag = _diag(None, events_fast)
        render_briefing(briefing(diff=diff, market=market_data, vix=vix_price, fg=fg,
                                 events=events_fast, diag=diag))
        st.markdown("##### 📈 총자산 추이 (과거 기록 + 현재)")
        render_total_trend(total_hist, accent, compact=True)
        st.divider()
        st.markdown("##### 🧭 방향성 한눈에")
        render_direction(diag, compact=True)
        st.caption("전체 점검표·스트레스 테스트는 **🧭 내 자산 진단** 메뉴에서 볼 수 있어요.")
        st.divider()
        st.markdown("##### 📄 직전 RAW 대비 — 무엇이 내 자산을 움직였나")
        render_raw_waterfall(diff, accent, compact=True)
        st.divider()
        st.markdown("##### 🥧 자산 구성")
        col_a, col_b = st.columns(2)
        with col_a:
            class_colors = {"해외주식": "#4C78A8", "암호화폐": "#F58518", "현금": "#54A24B"}
            _pie(allocation_by_class(all_rows_agg), "분류", "자산군", color_map=class_colors)
        with col_b:
            _pie(allocation_by_bucket(all_rows_agg), "자산버킷", "자산버킷 (성격별)")
        render_top_and_pnl(invest_rows, accent)
        with st.expander("👨‍👩‍👧 가족 자산 · 운용 총자산 상세", expanded=False):
            render_family_panel(family_df, net)

    # ===== 🧭 내 자산 진단 =====
    elif sel == NAV[1]:
        with st.spinner("시장 신호 불러오는 중…"):
            sig = market_signals()
        events_fast = upcoming_events(30, holding_tk, include_network=False)
        diag = _diag(sig, events_fast)
        st.caption("RAW를 올릴 때마다 자동으로 다시 계산되는 내 포트폴리오 점검표입니다. "
                   "매수·매도 권유가 아닌 판단 보조 정보예요.")
        render_direction(diag)
        st.divider()
        st.subheader("📄 이번 RAW 변화 상세")
        render_raw_waterfall(diff, accent)
        render_raw_change_tables(diff)
        st.divider()
        render_stress(stress, net)
        st.divider()
        st.subheader("🎯 노출 분석 — 실제로 시장에 얼마나 걸려 있나")
        diag["weighted_lev"] = float(lev.get("weighted", 0) or 0)
        render_exposure(diag, s)
        render_concentration(invest_rows)
        st.divider()
        st.subheader("⚖️ 목표비중 vs 현재")
        render_rebalance_chart(rebal, accent)
        render_rebalancing(invest_rows, meta_df)
        st.divider()
        st.subheader("💱 환율 노출 · 리스크 지표")
        render_currency_exposure(all_rows_agg, usd_krw)
        render_risk_panel(snapshots_df)

    # ===== 📰 시장·이슈 =====
    elif sel == NAV[2]:
        render_why_moved(s, invest_rows)
        st.divider()
        render_market_context()
        st.divider()
        with st.spinner("시장 분석 불러오는 중…"):
            news_items = get_market_news(10)
            ai_share = 0.0
            if not invest_rows.empty:
                inv_total = float(invest_rows["평가금액"].sum())
                if inv_total > 0 and "자산버킷" in invest_rows.columns:
                    ai_mask = invest_rows["자산버킷"].astype(str).str.contains(
                        "AI|반도체|빅테크", regex=True)
                    ai_share = float(invest_rows[ai_mask]["평가금액"].sum()) / inv_total
            bubble = compute_bubble_index(fg, news_items, vix_price,
                                          portfolio_ai_share=ai_share,
                                          portfolio_leverage=lev.get("weighted", 1.0))
        render_fear_greed_detail(fg)
        st.divider()
        render_bubble(bubble)
        st.divider()
        render_news(news_items)

    # ===== 📅 일정·전망 =====
    elif sel == NAV[3]:
        with st.spinner("일정·실적 발표일 불러오는 중…"):
            events = upcoming_events(62, holding_tk, include_network=True)
            sig = market_signals()
        render_events_section(events)
        render_econ_week()
        st.divider()
        render_outlook(sig, fg, events, stress)
        st.divider()
        render_claude_note()

    # ===== 🔍 종목 =====
    elif sel == NAV[4]:
        render_change_heatmap(invest_rows)
        st.divider()
        render_diagnostics(invest_rows)

    # ===== 📈 추이·보유 =====
    else:
        render_account_history(acc_hist)
        st.divider()
        render_timeseries(snapshots_df)
        render_monthly_heatmap(snapshots_df)
        st.divider()
        render_table(stock_rows, coin_rows, cash_rows)

    st.caption("ⓘ 모든 분석은 공개 시세·뉴스와 내 RAW 데이터 기반의 참고 정보이며 투자 자문이 아닙니다.")
    # 좌측 하단 실시간 인디케이터 (항상 표시)
    render_live_dot()


if __name__ == "__main__":
    main()
