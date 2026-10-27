"""화면 공통: 데이터 컨텍스트 캐시, 색(상태 의미 전용), 배지·카드 HTML, 입력 상태 관리."""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta

import streamlit as st

from agent.loader import load_context
from agent.models import MeasurementInput, VARIABLES
from agent.pipeline import default_time, latest_input

log = logging.getLogger("pump-ui")

NAVY = "#1F4E78"
# 상태 색은 의미 전용 (정상/관찰/주의/경고). 항상 텍스트 라벨과 함께 쓴다.
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
ON_STATUS = {"good": "#ffffff", "warning": "#0b0b0b", "serious": "#0b0b0b", "critical": "#ffffff"}
GRADE_ROLE = {"정상": "good", "관찰": "warning", "주의": "serious", "경고": "critical", "미입력": None}
STATUS_ROLE = {"정상": "good", "이상징후": "warning", "이상": "critical"}
LEVEL_ROLE = {"즉시": "critical", "긴급": "serious", "계획": "warning", "관찰": None}
ICON = {"good": "●", "warning": "▲", "serious": "◆", "critical": "■", None: "○"}

INPUT_FIELDS = [  # (키, 라벨, 단위, 필수, 정격 키, 소수 자릿수, 증분)
    ("discharge", "토출압력", "bar", True, "discharge", 2, 0.01),
    ("flow", "유량", "m³/h", True, "flow", 1, 0.1),
    ("current", "전류", "A", True, "current", 1, 0.1),
    ("vibration", "진동RMS", "mm/s", True, None, 2, 0.01),
    ("suction", "흡입압력", "bar", False, "suction", 2, 0.01),
    ("bearing_temp", "베어링온도", "℃", False, None, 1, 0.1),
]


def theme_colors() -> dict:
    """현재 테마(라이트/다크)에 맞는 차트 잉크·면 색."""
    dark = False
    try:
        dark = st.context.theme.type == "dark"
    except Exception:
        pass
    if dark:
        return {"dark": True, "series": "#3987e5", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
                "grid": "#2c2c2a", "axis": "#383835", "surface": "#1a1a19", "border": "rgba(255,255,255,0.10)"}
    return {"dark": False, "series": "#2a78d6", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
            "grid": "#e1e0d9", "axis": "#c3c2b7", "surface": "#fcfcfb", "border": "rgba(11,11,11,0.10)"}


@st.cache_resource(show_spinner="데이터를 불러오는 중…")
def get_ctx():
    ctx = load_context()
    _ = ctx.baseline
    return ctx


def esc(x) -> str:
    return html.escape(str(x))


def badge(text: str, role: str | None) -> str:
    if role is None:
        return f'<span class="pd-badge pd-neutral">{ICON[None]} {esc(text)}</span>'
    return (f'<span class="pd-badge" style="background:{STATUS[role]};color:{ON_STATUS[role]}">'
            f'{ICON[role]} {esc(text)}</span>')


def inject_css():
    c = theme_colors()
    st.markdown(f"""
<style>
.pd-badge {{display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.82rem;font-weight:600;
  line-height:1.6;white-space:nowrap}}
.pd-neutral {{border:1px solid {c['axis']};color:{c['ink2']}}}
.pd-cards {{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:4px 0 16px}}
.pd-card {{border:1px solid {c['border']};border-radius:10px;padding:14px 16px;background:var(--pd-card-bg, transparent)}}
.pd-card .k {{font-size:0.8rem;color:{c['muted']};margin-bottom:6px}}
.pd-card .v {{font-size:1.35rem;font-weight:700;color:{c['ink']};line-height:1.3}}
.pd-card .s {{font-size:0.82rem;color:{c['ink2']};margin-top:6px}}
.pd-scroll {{overflow-x:auto}}
.pd-table {{width:100%;border-collapse:collapse;font-size:0.88rem}}
.pd-table th, .pd-table td {{white-space:nowrap}}
.pd-table td.wrap {{white-space:normal}}
.pd-table th {{text-align:left;color:{c['muted']};font-weight:500;border-bottom:1px solid {c['axis']};padding:6px 8px}}
.pd-table td {{border-bottom:1px solid {c['grid']};padding:7px 8px;color:{c['ink']};font-variant-numeric:tabular-nums}}
.pd-bar {{height:8px;border-radius:4px;background:{c['grid']};overflow:hidden;margin:6px 0 2px}}
.pd-bar > div {{height:100%;border-radius:4px;background:{NAVY if not c['dark'] else '#5598e7'}}}
.pd-cand {{border:1px solid {c['border']};border-radius:10px;padding:14px 16px;height:100%}}
.pd-cand h4 {{margin:0 0 2px;font-size:1.02rem}}
.pd-cand .rank {{color:{c['muted']};font-size:0.8rem}}
.pd-cand ul {{padding-left:1.1rem;margin:6px 0 0;font-size:0.86rem;color:{c['ink2']}}}
.pd-steps {{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}}
.pd-step {{border:1px solid {c['axis']};border-radius:8px;padding:6px 10px;font-size:0.88rem;color:{c['ink']}}}
.pd-arrow {{color:{c['muted']}}}
.pd-err {{color:{STATUS['critical']};font-size:0.8rem;margin-top:-6px}}
.pd-ratio {{color:{c['muted']};font-size:0.8rem;margin-top:-8px;margin-bottom:6px}}
.pd-rec {{font-size:0.7rem;color:{NAVY if not c['dark'] else '#86b6ef'};border:1px solid currentColor;border-radius:6px;padding:0 5px;margin-left:4px}}
</style>""", unsafe_allow_html=True)


def card(key: str, value_html: str, sub: str = "") -> str:
    return f'<div class="pd-card"><div class="k">{esc(key)}</div><div class="v">{value_html}</div><div class="s">{sub}</div></div>'


# ---------------- 입력 상태 ----------------
def input_key(field: str) -> str:
    return f"in_{field}"


def init_state():
    """위젯 기본값은 세션 상태로만 관리한다 (위젯에 value 를 따로 주지 않음)."""
    ss = st.session_state
    for f, *_ in INPUT_FIELDS:
        ss.setdefault(input_key(f), None)
    ss.setdefault("signs", [])
    ss.setdefault("recheck_signs", [])
    ss.setdefault("use_default_time", True)
    ss.setdefault("errors", {})
    when = default_when()
    ss.setdefault("when_date", when.date())
    ss.setdefault("when_time", when.time())


def clear_inputs():
    for f, *_ in INPUT_FIELDS:
        st.session_state[input_key(f)] = None
    st.session_state["signs"] = []
    st.session_state["use_default_time"] = True
    st.session_state.pop("report", None)
    st.session_state["errors"] = {}


def load_latest_into_state(pump_id: str):
    ctx = get_ctx()
    inp = latest_input(ctx, pump_id)
    for f, *_ in INPUT_FIELDS:
        st.session_state[input_key(f)] = getattr(inp, f)
    st.session_state["use_default_time"] = False
    st.session_state["when_date"] = inp.measured_at.date()
    st.session_state["when_time"] = inp.measured_at.time()
    st.session_state["signs"] = []
    st.session_state["errors"] = {}


def measurement_from_state(pump_id: str) -> MeasurementInput:
    vals = {f: st.session_state.get(input_key(f)) for f, *_ in INPUT_FIELDS}
    when = None
    if not st.session_state.get("use_default_time", True):
        d, t = st.session_state.get("when_date"), st.session_state.get("when_time")
        if d is not None and t is not None:
            when = datetime.combine(d, t)
    return MeasurementInput(pump_id, vals["discharge"], vals["flow"], vals["current"], vals["vibration"],
                            vals["suction"], vals["bearing_temp"], when, list(st.session_state.get("signs") or []))


def input_snapshot(pump_id: str) -> tuple:
    """결과가 현재 입력으로 만든 것인지 비교하기 위한 입력 지문."""
    ss = st.session_state
    return (pump_id, tuple(ss.get(input_key(f)) for f, *_ in INPUT_FIELDS), tuple(sorted(ss.get("signs") or [])),
            ss.get("use_default_time", True), None if ss.get("use_default_time", True) else (ss.get("when_date"), ss.get("when_time")))


def default_when() -> datetime:
    return default_time(get_ctx()).to_pydatetime()


def fmt_eta(days) -> str:
    if days is None:
        return "-"
    return "이미 초과" if days == 0 else f"약 {days:.0f}일 뒤"


PAGES: dict = {}   # app.py가 st.Page 객체를 채운다 (페이지 간 이동용)
