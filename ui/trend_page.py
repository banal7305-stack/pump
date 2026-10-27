"""추세 대시보드: 항목별 작은 차트(축 1개씩) + 주의/경고 임계선 + 알람 마커 + 정비 작업선."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from agent.models import LABELS, UNITS
from ui.common import STATUS, get_ctx, theme_colors

PANELS = ["vibration", "bearing_temp", "current", "flow", "discharge", "suction"]
RANGES = {"7일": 7, "30일": 30, "90일": 90, "전체": None}
GRADE_NAME = {2: "주의", 3: "경고"}
GRADE_COLOR = {2: STATUS["serious"], 3: STATUS["critical"]}


def thresholds(ctx, pump_id: str) -> dict[str, list[tuple[float, int]]]:
    """변수 → [(임계값(원 단위), 등급)] — 주의(2)·경고(3)만."""
    rated = ctx.pump(pump_id).rated
    out: dict[str, list] = {v: [] for v in PANELS}
    for key, g in ctx.cfg["grades"].items():
        src = g.get("source", key)
        for k, b in enumerate(g["bounds"]):
            grade = k + 1
            if grade < 2:
                continue
            out[src].append((b * rated[src] if g["ratio"] else b, grade))
    return out


def build_figure(ctx, pump_id: str, days: int | None) -> go.Figure:
    c = theme_colors()
    end = ctx.data_end + timedelta(hours=2)
    start = end - timedelta(days=days) if days else ctx.ops["time"].min()
    df = ctx.ops[(ctx.ops["pump"] == pump_id) & (ctx.ops["time"] >= start)].copy()
    running = df["state"].eq("운전")
    alarms = ctx.alarms[(ctx.alarms["pump"] == pump_id) & (ctx.alarms["time"] >= start)]
    wos = ctx.work_orders[(ctx.work_orders["pump"] == pump_id) & (ctx.work_orders["date"] >= start)]
    code_var = ctx.cfg["alarms"]["code_variable"]
    th = thresholds(ctx, pump_id)

    fig = make_subplots(rows=len(PANELS), cols=1, shared_xaxes=True, vertical_spacing=0.035,
                        subplot_titles=[f"{LABELS[v]} ({UNITS[v]})" for v in PANELS])
    for r, v in enumerate(PANELS, start=1):
        y = df[v].where(running)          # 정지 구간은 선을 끊는다
        fig.add_trace(go.Scatter(x=df["time"], y=y, mode="lines", line=dict(color=c["series"], width=2),
                                 name=LABELS[v], showlegend=False,
                                 hovertemplate=f"%{{x|%m-%d %H:%M}}<br>{LABELS[v]} %{{y:.2f}} {UNITS[v]}<extra></extra>"),
                      row=r, col=1)
        for value, grade in th[v]:
            fig.add_hline(y=value, line=dict(color=GRADE_COLOR[grade], width=1, dash="dash"), row=r, col=1,
                          annotation_text=f"{GRADE_NAME[grade]} {value:.3g}", annotation_position="top left",
                          annotation_font=dict(size=10, color=c["ink2"]))
        # 임계값 알람: 해당 변수 패널에 값 위치로 표시
        a = alarms[alarms["code"].map(code_var).eq(v) & alarms["value"].notna()]
        if len(a):
            grade = np.where(a["grade"].eq("경고"), 3, 2)
            fig.add_trace(go.Scatter(
                x=a["time"], y=a["value"], mode="markers", showlegend=False, name="알람",
                marker=dict(size=9, color=[GRADE_COLOR[g] for g in grade], line=dict(color=c["surface"], width=2)),
                customdata=np.stack([a["alarm_id"], a["name"], a["threshold"].astype(str), a["note"].fillna("")], axis=1),
                hovertemplate="%{x|%m-%d %H:%M} %{customdata[0]}<br>%{customdata[1]}: %{y:.2f} (기준 %{customdata[2]})"
                              "<br>%{customdata[3]}<extra></extra>"), row=r, col=1)
    # 현장 알람(이상음·누수 등): 진동 패널에 마름모로 표시
    field = alarms[alarms["value"].isna() | ~alarms["code"].isin(code_var)]
    if len(field):
        vib = df.set_index("time")["vibration"].where(running.values)
        yv = [vib.asof(t) if len(vib.dropna()) else 0 for t in field["time"]]
        fig.add_trace(go.Scatter(
            x=field["time"], y=yv, mode="markers", showlegend=False, name="현장 알람",
            marker=dict(size=11, symbol="diamond", color=STATUS["warning"], line=dict(color=c["surface"], width=2)),
            customdata=np.stack([field["alarm_id"], field["name"], field["note"].fillna("")], axis=1),
            hovertemplate="%{x|%m-%d %H:%M} %{customdata[0]}<br>현장: %{customdata[1]}<br>%{customdata[2]}<extra></extra>"),
            row=1, col=1)
    # 정비 작업: 전 패널 세로선 + 맨 위 패널에 호버용 표식
    for _, w in wos.iterrows():
        fig.add_vline(x=w["date"], line=dict(color=c["muted"], width=1, dash="dot"))
    if len(wos):
        top = float(np.nanmax([df["vibration"].max(), 8.0]))
        fig.add_trace(go.Scatter(
            x=wos["date"], y=[top] * len(wos), mode="markers", showlegend=False, name="정비",
            marker=dict(size=10, symbol="triangle-down", color=c["muted"]),
            customdata=np.stack([wos["wo_id"], wos["work_type"], wos["part"], wos["action"], wos["cause"].astype(str)], axis=1),
            hovertemplate="%{x|%Y-%m-%d} %{customdata[0]} %{customdata[1]}<br>%{customdata[2]}: %{customdata[3]}"
                          "<br>원인: %{customdata[4]}<extra></extra>"), row=1, col=1)

    fig.update_layout(height=170 * len(PANELS) + 60, margin=dict(l=56, r=16, t=36, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=c["ink2"], size=12), hovermode="closest",
                      hoverlabel=dict(bgcolor=c["surface"], font_color=c["ink"], bordercolor=c["axis"]))
    for ann in fig.layout.annotations[: len(PANELS)]:   # 소제목 (make_subplots가 먼저 만든 주석)
        ann.update(x=0, xanchor="left", font=dict(color=c["ink"], size=13))
    fig.update_xaxes(showgrid=False, linecolor=c["axis"], tickfont=dict(color=c["muted"]), tickformat="%m-%d")
    fig.update_yaxes(automargin=True, gridcolor=c["grid"], zeroline=False, linecolor=c["axis"], tickfont=dict(color=c["muted"]))
    return fig


def page():
    ctx = get_ctx()
    pump_id = st.session_state.get("pump") or next(iter(ctx.pumps))
    p = ctx.pump(pump_id)
    st.title("추세 대시보드")
    st.caption(f"{p.pump_id} {p.name} · 선은 2시간 간격 운전값(정지 구간 끊김), 점선은 주의/경고 기준, "
               "원은 임계값 알람, 마름모는 현장 알람, ▼와 세로 점선은 정비 작업입니다.")
    choice = st.segmented_control("기간", list(RANGES), default="30일", key="trend_range")
    days = RANGES[choice or "30일"]
    with st.spinner("차트를 그리는 중…"):
        st.plotly_chart(build_figure(ctx, pump_id, days), width="stretch", theme=None,
                        config={"displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d"]})

    end = ctx.data_end + timedelta(hours=2)
    start = end - timedelta(days=days) if days else ctx.ops["time"].min()
    t1, t2, t3 = st.tabs(["알람 이력", "정비 이력", "데이터 표"])
    with t1:
        a = ctx.alarms[(ctx.alarms["pump"] == pump_id) & (ctx.alarms["time"] >= start)].sort_values("time", ascending=False)
        st.dataframe(a.rename(columns={"alarm_id": "알람ID", "time": "발생일시", "code": "코드", "name": "알람명", "grade": "등급",
                                       "value": "측정값", "threshold": "임계값", "cleared": "해제일시", "note": "비고"})
                     .drop(columns=["pump"]), hide_index=True, width="stretch")
    with t2:
        w = ctx.work_orders[(ctx.work_orders["pump"] == pump_id)].sort_values("date", ascending=False)
        st.dataframe(w.rename(columns={"wo_id": "작업ID", "date": "작업일자", "work_type": "유형", "part": "대상부품", "cause": "고장원인",
                                       "symptom": "작업 전 증상", "action": "조치내용", "hours": "시간(h)", "cost": "비용(원)", "note": "비고"})
                     .drop(columns=["pump"]), hide_index=True, width="stretch")
    with t3:
        d = ctx.ops[(ctx.ops["pump"] == pump_id) & (ctx.ops["time"] >= start)].sort_values("time", ascending=False)
        st.dataframe(d.rename(columns={"time": "일시", **{v: f"{LABELS[v]}({UNITS[v]})" for v in PANELS}, "state": "운전상태"})
                     .drop(columns=["pump"]), hide_index=True, width="stretch")
