"""전체 우선순위: 12대 최신값 일괄 진단 표, 행 선택 시 진단 페이지로 이동."""
from __future__ import annotations

import io

import streamlit as st

from agent.pipeline import run_batch
from agent.report import priority_rows, to_xlsx
from ui import common
from ui.common import LEVEL_ROLE, ON_STATUS, STATUS, STATUS_ROLE, badge, card, get_ctx


@st.cache_resource(show_spinner="12대 일괄 진단 중…")
def batch_reports():
    return run_batch(get_ctx(), use_llm=False)


def _cell_style(role):
    """이상 상태만 색으로 강조 (정상·관찰은 기본 글자색)."""
    if role == "good":
        return ""
    return f"background-color:{STATUS[role]};color:{ON_STATUS[role]};font-weight:600" if role else ""


def page():
    st.title("전체 우선순위")
    st.caption("각 펌프의 데이터 마지막 운전값으로 진단한 결과입니다. 행을 선택하면 해당 펌프의 진단 화면으로 이동합니다.")
    reps = batch_reports()
    df = priority_rows(reps)

    counts = df["등급"].value_counts()
    html = '<div class="pd-cards">'
    for _, level, deadline in get_ctx().cfg["priority"]["levels"]:
        html += card(f"{level} · {deadline}", f"{int(counts.get(level, 0))}대", badge(level, LEVEL_ROLE[level]))
    st.markdown(html + "</div>", unsafe_allow_html=True)

    show = df.drop(columns=["측정시각"]).copy()
    show["신뢰도(%)"] = show["신뢰도(%)"].map(lambda x: "-" if x != x or x is None else f"{x:.0f}")
    styler = (show.style
              .map(lambda v: _cell_style(STATUS_ROLE.get(v)), subset=["종합상태"])
              .map(lambda v: _cell_style(LEVEL_ROLE.get(v)), subset=["등급"])
              .format({"우선순위 점수": "{:.1f}"}))
    event = st.dataframe(styler, hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key=f"priority_table_{st.session_state.get('priority_ver', 0)}",
                         height=36 * (len(show) + 1) + 4,
                         column_config={
                             "우선순위 점수": st.column_config.ProgressColumn("우선순위 점수", min_value=0, max_value=100, format="%.1f"),
                             "1순위 원인": st.column_config.TextColumn("1순위 원인", width="medium"),
                             "설비명": st.column_config.TextColumn("설비명", width="medium"),
                             "첫 확인 항목": st.column_config.TextColumn("첫 확인 항목", width="large"),
                             "심각도": st.column_config.NumberColumn("심각도", width="small"),
                             "진행속도": st.column_config.NumberColumn("진행속도", width="small"),
                             "설비중요도": st.column_config.NumberColumn("중요도", width="small")})
    rows = event.selection.rows if event and event.selection else []
    if rows:
        # 사이드바 펌프 위젯은 이미 그려졌으므로, 이동 후 app.py에서 펌프 변경·진단을 처리한다
        st.session_state["pending_pump"] = df.iloc[rows[0]]["펌프ID"]
        st.session_state["priority_ver"] = st.session_state.get("priority_ver", 0) + 1   # 돌아왔을 때 선택 해제
        st.switch_page(common.PAGES["진단"])

    buf = io.BytesIO()
    to_xlsx(reps, buf)
    c1, c2 = st.columns([1, 3])
    c1.download_button("우선순위표 .xlsx", buf.getvalue(), "priority_all.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    if c2.button("다시 계산"):
        batch_reports.clear()
        st.rerun()
