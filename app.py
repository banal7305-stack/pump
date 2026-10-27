"""펌프 이상진단 에이전트 - Streamlit 화면.  실행: streamlit run app.py"""
import logging

import streamlit as st

st.set_page_config(page_title="펌프 이상진단", page_icon=":material/water_pump:", layout="wide")

from agent.loader import DataError  # noqa: E402
from ui import common  # noqa: E402
from ui.common import clear_inputs, get_ctx, init_state, inject_css, load_latest_into_state  # noqa: E402
from ui import diagnose_page, priority_page, trend_page  # noqa: E402

logging.basicConfig(level=logging.INFO)

try:
    ctx = get_ctx()
except DataError as e:
    st.error(f"데이터 파일을 불러오지 못했습니다.\n\n{e}")
    st.stop()

common.PAGES = {
    "진단": st.Page(diagnose_page.page, title="진단", icon=":material/stethoscope:", url_path="diagnose", default=True),
    "추세": st.Page(trend_page.page, title="추세 대시보드", icon=":material/monitoring:", url_path="trend"),
    "우선순위": st.Page(priority_page.page, title="전체 우선순위", icon=":material/format_list_numbered:", url_path="priority"),
}
nav = st.navigation(list(common.PAGES.values()))

init_state()
inject_css()
pumps = list(ctx.pumps)
st.session_state.setdefault("pump", "P-105" if "P-105" in pumps else pumps[0])
if "pending_pump" in st.session_state:          # 전체 우선순위에서 선택한 펌프
    pid = st.session_state.pop("pending_pump")
    st.session_state["pump"] = pid
    load_latest_into_state(pid)
    diagnose_page.run_and_store(pid)

with st.sidebar:
    st.selectbox("펌프 선택", pumps, key="pump", on_change=clear_inputs,
                 format_func=lambda p: f"{p} · {ctx.pump(p).name}")
    p = ctx.pump(st.session_state["pump"])
    r = p.rated
    st.markdown(
        f"**{p.name}**  \n{p.location} · {p.pump_type} · {p.kw:g} kW  \n"
        f"정격 유량 {r['flow']:g} m³/h · 토출 {r['discharge']:g} bar  \n"
        f"흡입 {r['suction']:g} bar · 전류 {r['current']:g} A  \n"
        f"설비중요도 {p.importance:g}")
    st.caption(f"데이터: {ctx.source.name} · {ctx.ops['time'].min():%Y-%m-%d} ~ {ctx.data_end:%Y-%m-%d}")

nav.run()
