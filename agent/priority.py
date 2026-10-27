"""F5 점검·교체 우선순위."""
from __future__ import annotations

import pandas as pd

from .diagnosis import confirmed_cases
from .loader import DataContext
from .models import CauseCandidate, DetectionResult, PriorityResult, PumpInfo


def level_for(cfg: dict, score: float) -> tuple[str, str]:
    for lim, name, deadline in cfg["priority"]["levels"]:
        if score >= lim:
            return name, deadline
    return cfg["priority"]["levels"][-1][1], cfg["priority"]["levels"][-1][2]


def prioritize(ctx: DataContext, pump: PumpInfo, det: DetectionResult, candidates: list[CauseCandidate],
               time: pd.Timestamp) -> PriorityResult:
    pcfg = ctx.cfg["priority"]
    severity = max(pcfg["severity_by_grade"][max(0, det.max_grade)], pcfg["severity_status_floor"][det.status])
    speed = det.trend.speed_score if det.status != "정상" else min(det.trend.speed_score, pcfg["neutral_speed"])
    top = candidates[0] if candidates else None
    comps = {"severity": float(severity), "speed": float(speed), "importance": float(pump.importance),
             "confidence": float(top.confidence) if top else 0.0}
    score = sum(pcfg["weights"][k] * comps[k] for k in comps)
    level, deadline = level_for(ctx.cfg, score)

    steps, parts, hours, cost, n = [], "-", None, None, 0
    if top:
        steps = ctx.mode(top.mode).steps
        cases = confirmed_cases(ctx, time)
        cases = cases[cases["mode"] == top.mode]
        n = len(cases)
        if n:
            parts = ", ".join(cases["part"].value_counts().index[:2])
            hours, cost = float(cases["hours"].mean()), float(cases["cost"].mean())
    return PriorityResult(round(score, 1), level, deadline, comps, steps, parts, hours, cost, n)
