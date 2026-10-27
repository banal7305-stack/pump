"""F3 원인 후보 분석: 지식베이스 패턴 매칭 + 현장 징후·알람 + 정비이력 유사 사례."""
from __future__ import annotations

import math
import re
from datetime import timedelta
from typing import Optional

import pandas as pd

from .loader import DataContext, FailureMode
from .models import (LABELS, RATIO_VARS, UNITS, VARIABLES, CauseCandidate, DetectionResult,
                     MeasurementInput, SimilarCase)

ARROWS = {2: "↑↑", 1: "↑", 0: "변화 없음", -1: "↓", -2: "↓↓"}


def pattern_level(cfg: dict, variable: str, deviation: float) -> float:
    """기준선 대비 변화량 → 연속 수준(-2~2). level1에서 1, level2에서 2."""
    lv = cfg["diagnosis"]["pattern_levels"][variable]
    a = abs(deviation)
    if a <= lv["level1"]:
        mag = a / lv["level1"]
    else:
        mag = 1 + (a - lv["level1"]) / (lv["level2"] - lv["level1"])
    return max(-2.0, min(2.0, mag if deviation >= 0 else -mag))


def observed_levels(ctx: DataContext, det: DetectionResult) -> dict[str, tuple[float, float]]:
    """변수 → (수준, 변화량). 비율 항목은 기준선 대비 상대 변화."""
    out = {}
    for g in det.grades:
        if g.value is None or g.baseline is None:
            continue
        if g.variable in RATIO_VARS:
            dev = (g.value - g.baseline) / g.baseline if g.baseline else 0.0
        else:
            dev = g.value - g.baseline
        out[g.variable] = (pattern_level(ctx.cfg, g.variable, dev), dev)
    return out


def _describe(g, dev: float) -> str:
    if g.variable in RATIO_VARS:
        return f"{g.label} {g.value:g} {UNITS[g.variable]} (평소 {g.baseline:.3g} 대비 {dev * 100:+.0f}%)"
    return f"{g.label} {g.value:g} {UNITS[g.variable]} (평소 {g.baseline:.3g} 대비 {dev:+.1f})"


def work_order_mode(ctx: DataContext, wo: pd.Series) -> Optional[str]:
    text = f"{wo['part']} {wo['action']}"
    for keyword, mode in ctx.cfg["diagnosis"]["work_order_modes"]:
        if keyword in text:
            return mode
    return None


def confirmed_cases(ctx: DataContext, before: pd.Timestamp) -> pd.DataFrame:
    """고장원인이 확인된 교체/수리 이력 (측정 시각 이전만)."""
    w = ctx.work_orders
    w = w[(w["date"] < before) & w["work_type"].isin(["교체", "수리"]) & (w["cause"].astype(str) != "-")].copy()
    w["mode"] = [work_order_mode(ctx, r) for _, r in w.iterrows()]
    return w[w["mode"].notna()]


def family(name: str) -> str:
    return re.sub(r"\s*#\d+$", "", str(name)).strip()


def _similar(ctx: DataContext, cases: pd.DataFrame, mode: str, pump_id: str, limit: int = 2) -> list[SimilarCase]:
    c = cases[cases["mode"] == mode].copy()
    if c.empty:
        return []
    fam = family(ctx.pump(pump_id).name)
    c["rank_pump"] = (c["pump"] != pump_id).astype(int)
    c["rank_fam"] = (c["pump"].map(lambda p: family(ctx.pump(p).name) if p in ctx.pumps else "") != fam).astype(int)
    c = c.sort_values(["rank_pump", "rank_fam", "date"], ascending=[True, True, False]).head(limit)
    return [SimilarCase(r["wo_id"], r["date"].to_pydatetime(), r["pump"], r["part"], str(r["cause"]),
                        str(r["symptom"]), str(r["action"]), float(r["hours"]), int(r["cost"]))
            for _, r in c.iterrows()]


def shape_levels(ctx: DataContext, det: DetectionResult) -> dict[str, float]:
    """z-score 방향 패턴: 데드존을 뺀 z를 가장 큰 값이 ±2가 되도록 정규화."""
    dz = ctx.cfg["diagnosis"]["shape_deadzone"]
    zs = {g.variable: math.copysign(max(0.0, abs(g.z) - dz), g.z) for g in det.grades if g.z is not None}
    peak = max((abs(z) for z in zs.values()), default=0.0)
    return {v: (2 * z / peak if peak > 0 else 0.0) for v, z in zs.items()}


def score_mode(ctx: DataContext, mode: FailureMode, det: DetectionResult, levels: dict,
               shapes: dict | None = None) -> tuple[float, list, list]:
    dcfg = ctx.cfg["diagnosis"]
    shapes = shapes if shapes is not None else shape_levels(ctx, det)
    num = den = snum = sden = 0.0
    evidence, mismatches = [], []
    for v in VARIABLES:
        exp = mode.expected.get(v)
        if exp is None or v not in levels:
            continue
        obs, dev = levels[v]
        w = dcfg["key_weight"] if exp != 0 else 1.0
        diff = abs(obs - exp)
        num += w * diff
        den += w
        if v in shapes:
            snum += w * abs(shapes[v] - exp)
            sden += w
        g = det.grade_of(v)
        if exp != 0 and diff < 1.0 and abs(obs) >= 0.5 and obs * exp > 0:
            evidence.append(f"{_describe(g, dev)} — 패턴 일치 ({ARROWS[exp]})")
        elif diff >= 1.0:
            want = ARROWS[exp] if exp != 0 else "변화 없음"
            mismatches.append(f"{_describe(g, dev)} — 이 고장이면 {want} 예상")
    sim = 1 - num / (4 * den) if den else 0.0
    shape = 1 - snum / (4 * sden) if sden else sim
    wshape = dcfg["shape_weight"]
    return (1 - wshape) * sim + wshape * shape, evidence, mismatches


def diagnose(ctx: DataContext, inp: MeasurementInput, det: DetectionResult, time: pd.Timestamp) -> list[CauseCandidate]:
    dcfg = ctx.cfg["diagnosis"]
    levels = observed_levels(ctx, det)
    shapes = shape_levels(ctx, det)
    cases = confirmed_cases(ctx, time)
    recent = ctx.alarms[(ctx.alarms["pump"] == inp.pump_id) & (ctx.alarms["time"] < time)
                        & (ctx.alarms["time"] >= time - timedelta(days=ctx.cfg["alarms"]["evidence_days"]))]
    recent_codes = set(recent["code"])
    fam = family(ctx.pump(inp.pump_id).name)
    same_fam_pumps = {p for p, info in ctx.pumps.items() if family(info.name) == fam}
    wrong = ctx.work_orders[(ctx.work_orders["pump"] == inp.pump_id) & (ctx.work_orders["date"] < time)
                            & (ctx.work_orders["date"] >= time - timedelta(days=dcfg["misdiagnosis_days"]))
                            & ctx.work_orders["note"].astype(str).str.contains(dcfg["misdiagnosis_keyword"])]
    n_missing = len(inp.missing())

    out = []
    for mode in ctx.modes:
        sim, evidence, mismatches = score_mode(ctx, mode, det, levels, shapes)
        score = sim
        notes = []
        for sign in det.field_signs:
            related = dcfg["sign_modes"].get(sign, [])
            if mode.mode in related:
                score += dcfg["sign_bonus"]
                evidence.append(f"현장 징후 '{sign}' ({det.sign_sources.get(sign, '입력')})")
            elif related:
                score -= dcfg["sign_penalty"]
        alarm_bonus = 0.0
        for code in sorted(recent_codes):
            if mode.mode in dcfg["alarm_modes"].get(code, []):
                alarm_bonus += dcfg["alarm_bonus"]
                evidence.append(f"최근 {ctx.cfg['alarms']['evidence_days']}일 알람 {code}")
        score += min(alarm_bonus, dcfg["alarm_bonus_max"])
        mine = cases[(cases["mode"] == mode.mode) & cases["pump"].isin(same_fam_pumps)]
        if len(mine):
            score += dcfg["history_bonus"]
            evidence.append(f"같은 기종({fam})에서 같은 고장 확인 이력 {len(mine)}건")
        for _, w in wrong.iterrows():
            if work_order_mode(ctx, w) == mode.mode:
                notes.append(f"{w['date']:%Y-%m-%d} {w['part']} 점검에서 이상 없음 ({w['wo_id']}, 오진단 이력) — 다른 원인을 먼저 확인하세요.")
                score -= dcfg["sign_penalty"]
        if mode.mode == "캐비테이션/임펠러 마모":
            notes.append("근본 원인(흡입 스트레이너 막힘·흡입밸브 개도·수위)을 먼저 확인하세요. 임펠러만 교체하면 재발할 수 있습니다.")
        score = max(0.0, min(1.0, score))
        out.append(CauseCandidate(mode.mode, round(score, 4), 0.0, "", evidence, mismatches,
                                  _similar(ctx, cases, mode.mode, inp.pump_id), notes))
    # 신뢰도: 후보 간 점수 비중 × 절대 적합도 × 선택 입력 누락 보정
    weights = [math.exp(c.score / dcfg["confidence_temperature"]) for c in out]
    total = sum(weights)
    keep = max(0.0, 1 - dcfg["missing_penalty"] * n_missing)
    for c, w in zip(out, weights):
        fit = min(1.0, c.score / dcfg["fit_reference"])
        c.confidence = round(100 * w / total * fit * keep, 1)
        c.confidence_label = next(lbl for lim, lbl in dcfg["confidence_labels"] if c.confidence / 100 >= lim)
    out.sort(key=lambda c: c.score, reverse=True)
    return out[: dcfg["top_n"]]
