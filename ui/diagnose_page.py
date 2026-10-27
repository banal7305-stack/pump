"""진단 페이지: 입력 폼 → 결과(요약 카드, 판정표, 원인 후보, 추가 확인, 우선순위, 다운로드)."""
from __future__ import annotations

import io

import streamlit as st

from agent.models import InputError, LABELS, UNITS
from agent.pipeline import run_diagnosis
from agent.report import blocks, to_docx, to_markdown, to_xlsx
from ui.common import (GRADE_ROLE, INPUT_FIELDS, LEVEL_ROLE, STATUS_ROLE, badge, card, clear_inputs, default_when,
                       esc, fmt_eta, get_ctx, input_key, input_snapshot, load_latest_into_state, log,
                       measurement_from_state)


def run_and_store(pump_id: str, extra_signs: list[str] | None = None):
    """버튼 콜백: 진단을 실행하고 결과/오류를 세션에 저장한다."""
    ctx = get_ctx()
    if extra_signs:
        st.session_state["signs"] = sorted(set(st.session_state.get("signs") or []) | set(extra_signs))
    st.session_state["errors"] = {}
    try:
        inp = measurement_from_state(pump_id)
        rep = run_diagnosis(inp, ctx, use_llm=True)
        st.session_state["report"] = rep
        st.session_state["report_pump"] = pump_id
        st.session_state["report_inputs"] = input_snapshot(pump_id)
    except InputError as e:
        st.session_state.pop("report", None)
        st.session_state["errors"] = {e.field or "_": str(e)}
    except Exception:
        log.exception("진단 실패")
        st.session_state.pop("report", None)
        st.session_state["errors"] = {"_": "진단 중 예상하지 못한 오류가 발생했습니다. 입력값을 확인한 뒤 다시 시도하세요."}


def input_form(pump_id: str):
    ctx = get_ctx()
    rated = ctx.pump(pump_id).rated
    errors = st.session_state.get("errors", {})

    top = st.columns([3, 1.2, 1])
    top[0].subheader("측정값 입력")
    top[1].button("최신값 불러오기", on_click=load_latest_into_state, args=(pump_id,), width="stretch",
                  help="선택한 펌프의 데이터 마지막 운전값으로 채웁니다.")
    top[2].button("초기화", on_click=clear_inputs, width="stretch")

    cols = st.columns(3)
    for i, (f, label, unit, required, rkey, nd, step) in enumerate(INPUT_FIELDS):
        with cols[i % 3]:
            title = f"{label} ({unit})" + ("" if required else " · 선택")
            # value=None: 칸을 비우면 0이 아니라 '미입력'(None)으로 남는다
            val = st.number_input(title, key=input_key(f), value=None, step=step, format=f"%.{nd}f",
                                  placeholder="필수" if required else "권장 (비우면 신뢰도↓)")
            if f in errors:
                st.markdown(f'<div class="pd-err">{esc(errors[f])}</div>', unsafe_allow_html=True)
            elif rkey and val is not None and rated.get(rkey):
                st.markdown(f'<div class="pd-ratio">정격 {rated[rkey]:g} {unit} 대비 <b>{val / rated[rkey] * 100:.0f}%</b></div>',
                            unsafe_allow_html=True)
            elif not required and val is None:
                st.markdown('<div class="pd-ratio">권장 입력 <span class="pd-rec">권장</span></div>', unsafe_allow_html=True)

    c1, c2 = st.columns([2, 1])
    with c1:
        st.pills("현장 징후 (확인된 것만 선택)", ctx.cfg["field_signs"], selection_mode="multi", key="signs")
    with c2:
        use_default = st.checkbox(f"측정일시 기본값 ({default_when():%Y-%m-%d %H:%M})", key="use_default_time")
        if not use_default:
            d1, d2 = st.columns(2)
            d1.date_input("측정일", key="when_date")
            d2.time_input("측정시각", key="when_time", step=7200)

    general = [errors[k] for k in ("_", "pump_id", "signs", "measured_at") if k in errors]
    if general:
        st.error(general[0])
    st.button("진단 실행", type="primary", on_click=run_and_store, args=(pump_id,), width="stretch")


def summary_cards(rep):
    det, pr = rep.detection, rep.priority
    top = rep.candidates[0] if rep.candidates else None
    etas = [(LABELS[v], e) for v, e in det.trend.eta_days.items() if e is not None]
    eta = min(etas, key=lambda x: x[1]) if etas else None
    html = '<div class="pd-cards">'
    html += card("종합 상태", badge(det.status, STATUS_ROLE[det.status]),
                 f"최대 등급 {['정상', '관찰', '주의', '경고'][max(0, det.max_grade)]} · 이상지수 {det.anomaly_index:.1f}")
    html += card("1순위 원인", esc(top.mode) if top else "이상 없음",
                 f"신뢰도 {esc(top.confidence_label)} ({top.confidence:.0f}%)" if top else "원인 분석 대상 아님")
    html += card("우선순위", badge(pr.level, LEVEL_ROLE[pr.level]), f"{esc(pr.deadline)} · 점수 {pr.score:.1f}")
    html += card("경고 도달 예상", esc(fmt_eta(eta[1])) if eta else "-",
                 f"{esc(eta[0])} 경고 기준" if eta else esc(det.trend.note or "상승 추세 없음"))
    st.markdown(html + "</div>", unsafe_allow_html=True)


def grade_table(rep):
    rows = []
    for g in rep.detection.grades:
        rows.append("<tr>" + "".join([
            f"<td>{esc(g.label)}</td>",
            f"<td>{'미입력' if g.value is None else f'{g.value:g} {UNITS[g.variable]}'}</td>",
            f"<td>{'-' if g.ratio is None else f'{g.ratio * 100:.0f}%'}</td>",
            f"<td>{'-' if g.baseline is None else f'{g.baseline:.3g}'}</td>",
            f"<td>{'-' if g.z is None else f'{g.z:+.1f}'}</td>",
            f"<td>{badge(g.grade_name, GRADE_ROLE[g.grade_name])}</td>"]) + "</tr>")
    st.markdown('<div class="pd-scroll"><table class="pd-table"><thead><tr><th>항목</th><th>값</th><th>정격 대비</th><th>평소 기준선</th>'
                '<th>z</th><th>등급</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>",
                unsafe_allow_html=True)


def candidate_cards(rep):
    if not rep.candidates:
        st.success("평소 운전 범위 안에 있어 원인 후보를 제시하지 않습니다.")
        return
    cols = st.columns(len(rep.candidates))
    for i, (col, c) in enumerate(zip(cols, rep.candidates)):
        with col:
            ev = "".join(f"<li>{esc(e)}</li>" for e in c.evidence[:6]) or "<li>근거 없음</li>"
            st.markdown(
                f'<div class="pd-cand"><div class="rank">{i + 1}순위</div><h4>{esc(c.mode)}</h4>'
                f'<div class="pd-bar" role="img" aria-label="신뢰도 {c.confidence:.0f}%"><div style="width:{c.confidence:.0f}%"></div></div>'
                f'<div class="rank">신뢰도 {esc(c.confidence_label)} · {c.confidence:.0f}%</div><ul>{ev}</ul></div>',
                unsafe_allow_html=True)
            if c.mismatches or c.notes:
                with st.expander("불일치·참고"):
                    for m in c.mismatches:
                        st.markdown(f"- 불일치: {m}")
                    for n in c.notes:
                        st.markdown(f"- 참고: {n}")
            if c.similar_cases:
                with st.expander(f"유사 정비 사례 {len(c.similar_cases)}건"):
                    for s in c.similar_cases:
                        st.markdown(f"**{s.wo_id}** {s.date:%Y-%m-%d} {s.pump_id} · {s.cause}  \n"
                                    f"증상: {s.symptom}  \n조치: {s.action} ({s.hours:g}h, {s.cost:,}원)")


def checks_section(rep, pump_id: str):
    ctx = get_ctx()
    st.markdown("##### 추가 확인 체크리스트")
    for c in rep.checks:
        st.checkbox(f"{c.order}. {c.item}", key=f"chk_{pump_id}_{c.order}_{c.item}", help=c.reason)
    if rep.candidates:
        remaining = [s for s in ctx.cfg["field_signs"] if s not in rep.detection.field_signs]
        st.pills("현장에서 새로 확인한 징후", remaining, selection_mode="multi", key="recheck_signs")
        st.button("다시 진단", on_click=lambda: run_and_store(pump_id, st.session_state.get("recheck_signs") or []),
                  help="선택한 현장 징후를 반영해 원인 순위를 다시 계산합니다.")


def priority_section(rep):
    pr = rep.priority
    st.markdown(f"##### 점검·교체 우선순위 {badge(pr.level, LEVEL_ROLE[pr.level])} "
                f"<span style='font-size:0.85rem'>{esc(pr.deadline)} · 점수 {pr.score:.1f}</span>", unsafe_allow_html=True)
    comp = pr.components
    w = get_ctx().cfg["priority"]["weights"]
    st.caption(f"심각도 {comp['severity']:.0f} · 진행 속도 {comp['speed']:.0f} · 설비 중요도 {comp['importance']:.0f} · "
               f"원인 신뢰도 {comp['confidence']:.0f} (가중치 {w['severity']:.2f} / {w['speed']:.2f} / "
               f"{w['importance']:.2f} / {w['confidence']:.2f})")
    if pr.work_steps:
        steps = '<span class="pd-arrow">→</span>'.join(
            f'<span class="pd-step">{i + 1}. {esc(s)}</span>' for i, s in enumerate(pr.work_steps))
        st.markdown(f'<div class="pd-steps">{steps}</div>', unsafe_allow_html=True)
    if pr.case_count:
        st.caption(f"교체·수리 대상(과거 사례): {pr.parts} · 평균 {pr.avg_hours:.1f}시간 · 평균 {pr.avg_cost:,.0f}원 (사례 {pr.case_count}건)")


def downloads(rep):
    ctx = get_ctx()
    bl = blocks(ctx, rep)
    stem = f"report_{rep.pump.pump_id}_{rep.measured_at:%Y%m%d_%H%M}"
    docx_buf, xlsx_buf = io.BytesIO(), io.BytesIO()
    to_docx(bl, docx_buf)
    to_xlsx([rep], xlsx_buf)
    c = st.columns(3)
    c[0].download_button("보고서 .md", to_markdown(bl).encode("utf-8"), f"{stem}.md", "text/markdown", width="stretch")
    c[1].download_button("보고서 .docx", docx_buf.getvalue(), f"{stem}.docx",
                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document", width="stretch")
    c[2].download_button("우선순위표 .xlsx", xlsx_buf.getvalue(), f"{stem}_priority.xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")


def results(rep):
    st.divider()
    st.subheader(f"진단 결과 · {rep.pump.pump_id} {rep.pump.name}")
    st.caption(f"측정 시각 {rep.measured_at:%Y-%m-%d %H:%M}")
    summary_cards(rep)
    if rep.detection.false_alarm_note:
        st.warning(rep.detection.false_alarm_note)
    st.markdown(f"**종합 의견** <span style='font-size:0.78rem;opacity:0.7'>({'Claude 작성' if rep.opinion_source == 'llm' else '규칙 기반 템플릿'})</span>",
                unsafe_allow_html=True)
    st.write(rep.opinion)

    left, right = st.columns([1.15, 1])
    with left:
        st.markdown("##### 항목별 판정")
        grade_table(rep)
    with right:
        st.markdown("##### 추세·알람")
        t = rep.detection.trend
        if t.available:
            st.caption(f"최근 {get_ctx().cfg['trend']['window_days']}일 운전 {t.points}건 · 진행 속도 점수 {t.speed_score:.0f}")
        else:
            st.caption(t.note)
        if rep.detection.alarms:
            rows = "".join(f"<tr><td>{esc(a.code)}</td><td class='wrap'>{esc(a.name)}</td><td>{a.count}</td><td>{a.consecutive_days}일</td>"
                           f"<td>{', '.join(f'{v:g}' for v in a.values[-3:]) or '-'}</td><td>{'상승' if a.rising else '-'}</td>"
                           f"<td>{a.suspected_false or '-'}</td></tr>" for a in rep.detection.alarms)
            days = get_ctx().cfg["alarms"]["context_days"]
            st.markdown(f'<div class="pd-scroll"><table class="pd-table"><thead><tr><th>코드</th><th>알람명</th><th>{days}일</th><th>연속</th>'
                        '<th>최근 값</th><th>추세</th><th>오경보</th></tr></thead><tbody>' + rows + "</tbody></table></div>", unsafe_allow_html=True)
        else:
            st.caption(f"최근 {get_ctx().cfg['alarms']['context_days']}일 알람 없음")
        if rep.detection.field_signs:
            st.caption("현장 징후: " + ", ".join(f"{s} ({rep.detection.sign_sources[s]})" for s in rep.detection.field_signs))

    st.markdown("##### 원인 후보")
    candidate_cards(rep)
    left, right = st.columns(2)
    with left:
        checks_section(rep, rep.pump.pump_id)
    with right:
        priority_section(rep)
    st.markdown("##### 보고서 다운로드")
    downloads(rep)


def page():
    ctx = get_ctx()
    pump_id = st.session_state.get("pump") or next(iter(ctx.pumps))
    st.title("펌프 이상진단")
    input_form(pump_id)
    rep = st.session_state.get("report")
    if rep is not None and st.session_state.get("report_pump") == pump_id:
        if st.session_state.get("report_inputs") != input_snapshot(pump_id):
            st.info("입력값이 바뀌었습니다. [진단 실행]을 눌러 다시 진단하세요. (이전 결과는 숨김)")
        else:
            results(rep)
