"""F4 추가 확인 항목: 미입력 항목 → 1·2순위 구별 항목 → 후보별 지식베이스 확인 항목."""
from __future__ import annotations

from .loader import DataContext
from .models import CauseCandidate, CheckItem, DetectionResult, MeasurementInput


def checks_for(ctx: DataContext, inp: MeasurementInput, det: DetectionResult,
               candidates: list[CauseCandidate]) -> list[CheckItem]:
    vcfg = ctx.cfg["verification"]
    items: list[tuple[str, str]] = []

    def add(item: str, reason: str):
        if item not in (i for i, _ in items):
            items.append((item, reason))

    for v in inp.missing():
        add(vcfg["missing_items"][v], "미입력 항목")
    if det.status != "정상" and not det.field_signs:
        add(vcfg["missing_items"]["signs"], "미입력 항목")
    if det.false_alarm_note:
        add("동일 조건에서 재측정 (계측 오류·순간 변동 배제)", "입력값 급변")
    if not candidates:
        return [CheckItem(i + 1, it, rs) for i, (it, rs) in enumerate(items)]

    if len(candidates) >= 2:
        pair = {candidates[0].mode, candidates[1].mode}
        for d in vcfg["discriminators"]:
            if set(d["modes"]) == pair:
                for it in d["items"]:
                    add(it, f"{candidates[0].mode} / {candidates[1].mode} 구별")
    for c in candidates[:2]:
        for it in ctx.mode(c.mode).checks:
            add(it, f"{c.mode} 확인")
    return [CheckItem(i + 1, it, rs) for i, (it, rs) in enumerate(items[: vcfg["max_items"]])]
