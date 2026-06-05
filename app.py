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
        submitted = st.form_submit_button("입장", use_container_width=True)
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
            if st.button("🔄 시장지표 새로고침", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
            if fx_err:
                st.error(fx_err)
            return

        # 로컬(PC): 외부 접속 링크 + 데이터 폴더
        st.header("📱 외부 접속")
        ext = public_url()
        if ext:
            st.success(f"✅ {ext}")
            tlog_mtime = tunnel_log_mtime()
            if tlog_mtime:
                st.caption(f"터널 시작: {tlog_mtime:%Y-%m-%d %H:%M}")
            st.caption("폰/태블릿 브라우저에서 위 주소로 접속 → 같은 비밀번호 입력")
        else:
            st.caption("ℹ️ 24시간 폰 접속은 클라우드 주소를 사용하세요 (PC가 꺼져도 작동).")

        st.divider()

        st.header("⚙️ 데이터")
        st.metric("USD / KRW", f"{usd_krw:,.2f}", help="60초마다 자동 갱신")

        if st.button("🔄 시세 새로고침", use_container_width=True):
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
        "🟩 운용 총자산",
        fmt_krw(fs["total_managed"]),
        delta=f"가족 {fs['family_share']*100:.1f}%",
        delta_color="off",
        help="내 순자산 + 가족 자산 (보증금 + 다은 투자자산)",
    )
    c2.metric(
        "🟦 내 순자산",
        fmt_krw(s["total_eval"]),
        delta=f"수익률 {s['total_return']*100:+.2f}%",
        help="신한·업비트 RAW 기준, 부채(대출) 차감",
    )
    c3.metric("📈 일간 변동", fmt_pct(s["day_change"]))
    c4.metric("📈 주간 변동", fmt_pct(s["week_change"]))

    # 2행: 시장 컨디션
    m1, m2, m3, m4 = st.columns(4)
    if fg:
        m1.metric(
            f"😨 CNN F&G {fg['label']}",
            f"{fg['score']:.0f} / 100",
            delta=f"1주 전 {fg['prev_week']:.0f}" if fg.get("prev_week") else None,
            delta_color="off",
            help=f"CNN Fear & Greed Index. {fg['advice']}",
        )
    else:
        m1.metric("😨 CNN F&G", "—", help="API 응답 없음")

    if vix is not None:
        m2.metric(f"{vix_color(vix)} VIX 변동성",
                  f"{vix:,.2f}",
                  help="20↑ 🟡 주의 · 30↑ 🔴 위험")
    else:
        m2.metric("VIX 변동성", "—")

    if regime:
        m3.metric("S&P500 국면", regime.get("status", "—"),
                  delta=f"50일선 {regime['vs_ma50_pct']*100:+.2f}%",
                  delta_color="off")
    else:
        m3.metric("S&P500 국면", "—")

    m4.metric("🌡️ 투자 컨디션", temp["label"] if temp else "—",
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

    # 시장 국면 + 투자 컨디션
    regime = get_market_regime("^GSPC")
    vix_price = data.get("vix", {}).get("price")
    if regime:
        temp = market_temperature(vix_price, regime.get("status", ""))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("S&P500 국면", regime.get("status", "—"),
                  help=f"50일 이평 {regime['ma50']:,.0f} / 200일 이평 {regime['ma200']:,.0f} (있을 때)")
        c2.metric("S&P 50일선 대비", f"{regime['vs_ma50_pct']*100:+.2f}%")
        c3.metric("S&P 200일선 대비", f"{regime['vs_ma200_pct']*100:+.2f}%")
        c4.metric("투자 컨디션", temp["label"], help=temp["advice"])
        if temp["advice"]:
            st.caption(f"💡 **{temp['label']}** — {temp['advice']}")


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
        st.plotly_chart(gauge, use_container_width=True)

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
        st.plotly_chart(fig, use_container_width=True)

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
    st.plotly_chart(gauge, use_container_width=True)
    if fg.get("advice"):
        st.info(f"📌 **투자 시사**: {fg['advice']}")


def render_kpis(s: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("순자산 (부채차감)", fmt_krw(s["total_eval"]),
              delta=f"총자산 {fmt_krw(s['gross_assets'])}",
              delta_color="off")
    c2.metric("투자 수익률", fmt_pct(s["total_return"]),
              delta=fmt_krw(s["total_pnl"]))
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
    st.plotly_chart(fig, use_container_width=True)


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


def render_top_and_pnl(invest_rows: pd.DataFrame) -> None:
    if invest_rows.empty:
        return
    left, right = st.columns(2)

    with left:
        st.subheader("상위 종목 TOP 5")
        top = invest_rows.nlargest(5, "평가금액")[["종목명", "평가금액"]]
        fig = px.bar(top, x="평가금액", y="종목명", orientation="h", text="평가금액")
        fig.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
        fig.update_layout(yaxis=dict(autorange="reversed"),
                          margin=dict(t=10, b=10, l=10, r=10), height=320)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("종목별 손익 기여")
        df = pnl_contribution(invest_rows)
        df["color"] = df["손익"].apply(lambda x: "#54A24B" if x >= 0 else "#E45756")
        fig = go.Figure(go.Bar(
            x=df["손익"], y=df["종목명"], orientation="h",
            marker_color=df["color"],
            text=[f"{v:+,.0f}" for v in df["손익"]],
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>%{x:+,.0f}원<extra></extra>",
        ))
        fig.update_layout(yaxis=dict(autorange="reversed"),
                          margin=dict(t=10, b=10, l=10, r=10), height=320)
        st.plotly_chart(fig, use_container_width=True)


def render_family_panel(family_df: pd.DataFrame, my_net_eval: float) -> None:
    """가족 자산 + 운용 총자산 패널.

    내 투자자산 KPI 바로 옆에 따로 영역. 사용자가 한눈에 본인+가족 운용 규모 파악.
    """
    fs = family_summary(family_df, my_net_eval)

    st.subheader("👨‍👩‍👧 가족 자산 · 운용 총자산")
    st.caption("`portfolio.xlsx`의 **family** 시트에 항목별 금액 입력. "
               "**대출/부채**는 항목명에 '대출'을 넣으면 자동 차감됩니다 (금액은 양수로 입력).")

    # 상단 합계 KPI — 자산 / 대출 / 운용총자산
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟦 내 순자산 (RAW)", fmt_krw(my_net_eval),
              help="신한·업비트 RAW에서 산출된 부채차감 순자산")
    c2.metric("🟨 가족 자산", fmt_krw(fs["family_assets"]),
              help="보증금·투자자산 등 가족 보유자산 합계 (대출 제외)")
    if fs["family_debt"] > 0:
        c3.metric("🟥 대출 (부채)", f"-{fs['family_debt']:,.0f}원",
                  help="family 시트에서 '대출' 항목으로 인식된 금액. 운용 총자산에서 차감.")
    else:
        c3.metric("🟥 대출 (부채)", "없음",
                  help="family 시트에 '대출' 항목을 추가하면 여기 차감 표시됩니다.")
    c4.metric("🟩 운용 총자산", fmt_krw(fs["total_managed"]),
              delta="내 자산 + 가족 자산 − 대출",
              delta_color="off",
              help="내 순자산 + 가족 자산 − 대출. 실제 순운용 규모.")

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
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
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
                st.plotly_chart(fig, use_container_width=True)
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

    st.subheader("⚖️ 리밸런싱 시그널")
    st.caption("목표비중 ±5%p 이탈 시 매수/매도 권장. 음수 필요액 = 매도.")

    view = sig.copy()
    view["현재비중"] = (view["현재비중"] * 100).map(lambda x: f"{x:.2f}%")
    view["목표비중"] = (view["목표비중"] * 100).map(lambda x: f"{x:.1f}%")
    view["차이(%p)"] = (view["차이(%p)"] * 100).map(lambda x: f"{x:+.2f}")
    view["필요액(KRW)"] = view["필요액(KRW)"].map(lambda x: f"{x:+,.0f}")
    st.dataframe(view, use_container_width=True, hide_index=True)


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
    st.dataframe(view, use_container_width=True, hide_index=True)


def render_account_history(acc_hist: pd.DataFrame) -> None:
    """계좌별 자산 증감 추이 — RAW 파일 전체를 시계열로."""
    st.subheader("🏦 계좌별 자산 추이")
    if acc_hist is None or acc_hist.empty:
        st.info("RAW 파일이 누적되면 계좌별 추이가 표시됩니다.")
        return

    n_ts = acc_hist["ts"].nunique()
    if n_ts < 2:
        st.info(f"📅 RAW 파일이 2개 이상 쌓이면 추이가 그려집니다. (현재 {n_ts}개 시점) "
                "RAW 폴더에 새 파일을 떨어뜨릴 때마다 한 점씩 추가됩니다.")
        return

    pivot = acc_hist.pivot_table(index="ts", columns="account",
                                 values="eval_krw", aggfunc="last").sort_index()

    tab1, tab2 = st.tabs(["💰 평가액 (원)", "📊 증감률 (%, 첫 시점=0)"])

    account_colors = ["#4C78A8", "#F58518", "#54A24B", "#B279A2", "#9D755D",
                      "#72B7B2", "#E45756", "#EECA3B"]

    with tab1:
        fig = go.Figure()
        for i, acc in enumerate(pivot.columns):
            fig.add_trace(go.Scatter(
                x=pivot.index, y=pivot[acc], name=acc, mode="lines+markers",
                line=dict(width=2, color=account_colors[i % len(account_colors)]),
            ))
        fig.update_layout(hovermode="x unified", height=400,
                          yaxis_tickformat=",.0f", yaxis_title="평가액(원)",
                          margin=dict(t=20, b=20, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        norm = pivot.copy()
        for acc in norm.columns:
            base = norm[acc].dropna().iloc[0] if not norm[acc].dropna().empty else None
            norm[acc] = (norm[acc] / base - 1) * 100 if base else norm[acc]
        fig = go.Figure()
        for i, acc in enumerate(norm.columns):
            fig.add_trace(go.Scatter(
                x=norm.index, y=norm[acc], name=acc, mode="lines+markers",
                line=dict(width=2, color=account_colors[i % len(account_colors)]),
            ))
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(hovermode="x unified", height=400,
                          yaxis_title="증감률(%)", yaxis_ticksuffix="%",
                          margin=dict(t=20, b=20, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

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
    st.plotly_chart(fig, use_container_width=True)


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
    st.plotly_chart(fig, use_container_width=True)


def render_timeseries() -> None:
    st.subheader("시계열 추이")
    df = load_snapshots()
    if df.empty or len(df) < 2:
        st.info("📅 데이터가 누적되면 추이 그래프가 표시됩니다. (앱을 실행할 때마다 하루 단위로 자동 저장됨)")
        if not df.empty:
            st.caption(f"현재 누적 스냅샷: {len(df)}건")
        return

    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 순자산·총자산", "💰 자산군별", "📊 수익률·환율", "📉 일별 증감률"
    ])

    with tab1:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["date"], y=df["gross_assets"],
                                 name="총자산", mode="lines+markers",
                                 line=dict(color="#4C78A8", width=2)))
        fig.add_trace(go.Scatter(x=df["date"], y=df["total_eval"],
                                 name="순자산(부채차감)", mode="lines+markers",
                                 line=dict(color="#E45756", width=2)))
        fig.update_layout(hovermode="x unified", height=380,
                          yaxis_tickformat=",.0f",
                          margin=dict(t=20, b=20, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("💡 정규화 비교(내 포폴 vs S&P500)는 '수익률·환율' 탭에서 확인.")

    with tab2:
        long = df.melt(id_vars=["date"],
                       value_vars=["stock_eval", "coin_eval", "cash_eval"],
                       var_name="자산군", value_name="평가금액")
        long["자산군"] = long["자산군"].map({
            "stock_eval": "해외주식", "coin_eval": "암호화폐", "cash_eval": "현금"
        })
        fig = px.area(long, x="date", y="평가금액", color="자산군",
                      color_discrete_map={"해외주식": "#4C78A8",
                                          "암호화폐": "#F58518",
                                          "현금": "#54A24B"})
        fig.update_layout(hovermode="x unified", height=380,
                          yaxis_tickformat=",.0f",
                          margin=dict(t=20, b=20, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

    with tab3:
        fig = go.Figure()
        # 내 포트폴리오 누적 수익률을 시작점 0으로 정규화 (스냅샷 첫날 = 0%)
        base = df["total_eval"].iloc[0]
        my_norm = (df["total_eval"] / base - 1) * 100 if base else df["total_return"] * 100

        fig.add_trace(go.Scatter(x=df["date"], y=my_norm,
                                 name="내 포폴 누적(%)", yaxis="y1",
                                 line=dict(color="#54A24B", width=2)))

        spx_norm = _spx_normalized(df["date"])
        if spx_norm is not None:
            fig.add_trace(go.Scatter(x=df["date"], y=spx_norm,
                                     name="S&P500(%)", yaxis="y1",
                                     line=dict(color="#4C78A8", width=1, dash="dash")))

        fig.add_trace(go.Scatter(x=df["date"], y=df["usd_krw"],
                                 name="USD/KRW", yaxis="y2",
                                 line=dict(color="#9D755D", width=1, dash="dot")))
        fig.update_layout(
            hovermode="x unified", height=380,
            yaxis=dict(title="수익률(%)", side="left"),
            yaxis2=dict(title="환율", side="right", overlaying="y"),
            margin=dict(t=20, b=20, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab4:
        d = df.copy()
        d["daily_pct"] = d["total_eval"].pct_change() * 100
        d = d.dropna(subset=["daily_pct"])
        if d.empty:
            st.info("스냅샷이 더 누적되면 일별 증감률이 표시됩니다.")
            return
        colors = ["#E45756" if v < 0 else "#54A24B" for v in d["daily_pct"]]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=d["date"], y=d["daily_pct"],
            marker_color=colors,
            name="일별 변동(%)",
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:+.2f}%<extra></extra>",
        ))
        if len(d) >= 7:
            d["ma7"] = d["daily_pct"].rolling(7, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=d["date"], y=d["ma7"], name="7일 이평",
                line=dict(color="#9D755D", width=2),
            ))
        fig.update_layout(hovermode="x unified", height=380,
                          yaxis_title="일별 변동률(%)",
                          margin=dict(t=20, b=20, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)


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
                         use_container_width=True, hide_index=True)

    with tabs[1]:
        if stock_rows.empty:
            st.info("해외주식 보유내역이 없습니다.")
        else:
            account_filter = st.radio("계좌", ["전체"] + accounts,
                                      horizontal=True, key="acc_filter")
            df = stock_rows if account_filter == "전체" \
                else stock_rows[stock_rows["계좌"] == account_filter]
            st.dataframe(_format_table(df, show_account=True),
                         use_container_width=True, hide_index=True)

    with tabs[2]:
        if coin_rows.empty:
            st.info("암호화폐 보유내역이 없습니다.")
        else:
            st.dataframe(_format_table(coin_rows, show_account=False),
                         use_container_width=True, hide_index=True)

    with tabs[3]:
        if cash_rows.empty:
            st.info("현금/부채 내역이 없습니다.")
        else:
            st.dataframe(_format_table(cash_rows, show_account=False),
                         use_container_width=True, hide_index=True)
            st.caption("💡 대출잔액은 음수(-)로 입력. 자동으로 부채에 잡힙니다.")


def main() -> None:
    st.title("📊 내 포트폴리오")
    st.caption(f"마지막 조회: {datetime.now():%Y-%m-%d %H:%M:%S}")

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

    # 스냅샷 자동 저장 (로컬에서만 — 클라우드는 읽기 전용)
    if not cloud:
        try:
            append_snapshot(s, all_rows_agg, usd_krw,
                            leverage=lev, ratio=lr, vix=vix_price)
        except Exception as e:
            st.sidebar.warning(f"스냅샷 저장 실패: {e}")

    snapshots_df = data.get("snapshots")
    if snapshots_df is None:
        snapshots_df = load_snapshots()

    # ---------- 상단 헤더 (항상 표시) ----------
    render_top_header(s, fs, fg, vix_price, regime, temp)
    st.divider()

    # ---------- 탭 6개 ----------
    tab_summary, tab_market, tab_stock, tab_rebal, tab_risk, tab_trend = st.tabs([
        "📊 요약",
        "🌐 시장",
        "🩺 종목 분석",
        "⚖️ 리밸런싱",
        "📐 리스크",
        "📉 추이·보유",
    ])

    # ===== 📊 요약 =====
    with tab_summary:
        st.markdown("##### 🏦 자산 합계")
        render_kpis(s)
        st.divider()
        render_family_panel(family_df, float(s.get("total_eval", 0)))
        st.divider()
        st.markdown("##### 🥧 자산군 비중")
        col_a, col_b = st.columns(2)
        with col_a:
            from core.calc import allocation_by_class as _ac, allocation_by_kind as _ak
            class_colors = {"해외주식": "#4C78A8", "암호화폐": "#F58518", "현금": "#54A24B"}
            _pie(_ac(all_rows_agg), "분류", "자산군", color_map=class_colors)
        with col_b:
            kind_colors = {"개별주": "#4C78A8", "ETF": "#72B7B2", "코인": "#F58518", "현금": "#54A24B"}
            _pie(_ak(all_rows_agg), "종류", "종류별", color_map=kind_colors)
        st.divider()
        render_top_and_pnl(invest_rows)

    # ===== 🌐 시장 (무거운 뉴스·버블은 이 탭에서만 계산 → 첫 로딩 속도 개선) =====
    with tab_market:
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
        render_bubble(bubble)
        st.divider()
        render_fear_greed_detail(fg)
        st.divider()
        render_news(news_items)

    # ===== 🩺 종목 분석 =====
    with tab_stock:
        render_change_heatmap(invest_rows)
        st.divider()
        render_diagnostics(invest_rows)

    # ===== ⚖️ 리밸런싱 =====
    with tab_rebal:
        render_rebalancing(invest_rows, meta_df)
        st.divider()
        st.markdown("##### 🥧 자산버킷 · 종류별 비중")
        col1, col2 = st.columns(2)
        with col1:
            _pie(allocation_by_bucket(all_rows_agg), "자산버킷", "자산버킷 (성격별)")
        with col2:
            kind_colors = {"개별주": "#4C78A8", "ETF": "#72B7B2", "코인": "#F58518", "현금": "#54A24B"}
            _pie(allocation_invest_only(all_rows_agg), "분류", "주식 vs 코인 (투자만)",
                 color_map={"해외주식": "#4C78A8", "암호화폐": "#F58518"})

    # ===== 📐 리스크 =====
    with tab_risk:
        render_concentration(invest_rows)
        render_leverage_kpis(invest_rows, s)
        st.divider()
        render_currency_exposure(all_rows_agg, usd_krw)
        st.divider()
        render_risk_panel(snapshots_df)

    # ===== 📉 추이·보유 =====
    with tab_trend:
        render_account_history(acc_hist)
        st.divider()
        render_timeseries()
        render_monthly_heatmap(snapshots_df)
        st.divider()
        render_table(stock_rows, coin_rows, cash_rows)


if __name__ == "__main__":
    main()
