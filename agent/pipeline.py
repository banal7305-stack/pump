"""① 로드 → ② 탐지 → ③ 원인 → ④ 추가 확인 → ⑤ 우선순위 → ⑥ 보고서."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

import pandas as pd

from .baseline import running_mask
from .detection import detect, is_stopped, validate_input
from .diagnosis import diagnose
from .loader import DataContext, load_context
from .models import DiagnosisReport, InputError, MeasurementInput
from .priority import prioritize
from .report import write_opinion
from .verification import checks_for


log = logging.getLogger(__name__)


def default_time(ctx: DataContext) -> pd.Timestamp:
    """측정일시 미입력 시: 데이터 마지막 시각 다음 포인트."""
    return ctx.data_end + timedelta(hours=2)


def measurement_time(ctx: DataContext, inp: MeasurementInput) -> pd.Timestamp:
    """측정일시 정규화(시간대 제거)와 범위 확인."""
    if inp.measured_at is None:
        return default_time(ctx)
    try:
        t = pd.Timestamp(inp.measured_at)
    except (TypeError, ValueError):
        raise InputError(f"측정일시 형식이 올바르지 않습니다: {inp.measured_at!r}", "measured_at")
    if t.tzinfo is not None:
        t = t.tz_localize(None)          # 현장 시각(벽시계 기준)으로 해석
    start = ctx.ops["time"].min()
    limit = ctx.data_end + timedelta(days=ctx.cfg["validation"]["max_future_days"])
    if t <= start:
        raise InputError(f"측정일시가 데이터 시작({start:%Y-%m-%d}) 이전이라 기준 이력이 없습니다.", "measured_at")
    if t > limit:
        raise InputError(f"측정일시가 데이터 마지막 시각보다 {ctx.cfg['validation']['max_future_days']}일 넘게 뒤입니다. "
                         f"{limit:%Y-%m-%d} 이전으로 입력하세요.", "measured_at")
    return t


def run_diagnosis(inp: MeasurementInput, ctx: Optional[DataContext] = None, use_llm: bool = True) -> DiagnosisReport:
    ctx = ctx or load_context()
    validate_input(ctx, inp)
    if is_stopped(ctx, inp):
        raise InputError("유량과 전류가 거의 0이라 정지 상태로 보입니다. 운전 중에 측정한 값을 입력하세요.", "flow")
    time = measurement_time(ctx, inp)
    pump = ctx.pump(inp.pump_id)
    det = detect(ctx, inp, time)
    candidates = diagnose(ctx, inp, det, time) if det.status != "정상" else []
    checks = checks_for(ctx, inp, det, candidates)
    pr = prioritize(ctx, pump, det, candidates, time)
    rep = DiagnosisReport(pump, inp, time.to_pydatetime(), det, candidates, checks, pr)
    write_opinion(ctx, rep, use_llm)
    return rep


def latest_input(ctx: DataContext, pump_id: str) -> MeasurementInput:
    """펌프의 마지막 운전 포인트를 입력값으로 만든다 (측정 시각 = 그 포인트 시각)."""
    df = ctx.ops[(ctx.ops["pump"] == pump_id)]
    df = df[running_mask(df)]
    if df.empty:
        raise InputError(f"{pump_id}의 운전 이력이 없습니다.", "pump_id")
    r = df.iloc[-1]
    return MeasurementInput(pump_id, float(r["discharge"]), float(r["flow"]), float(r["current"]),
                            float(r["vibration"]), float(r["suction"]), float(r["bearing_temp"]),
                            r["time"].to_pydatetime())


def run_batch(ctx: Optional[DataContext] = None, use_llm: bool = False) -> list[DiagnosisReport]:
    ctx = ctx or load_context()
    reports = []
    for p in ctx.pumps:
        try:
            reports.append(run_diagnosis(latest_input(ctx, p), ctx, use_llm))
        except InputError as e:          # 한 대의 데이터 문제로 전체 표가 실패하지 않게
            log.warning("%s 일괄 진단 제외: %s", p, e)
    return sorted(reports, key=lambda r: r.priority.score, reverse=True)
