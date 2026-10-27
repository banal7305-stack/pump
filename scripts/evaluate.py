"""PRD 3.3 KPI 평가. 정답(상태라벨·오경보·이상이벤트)은 채점에만 쓴다.

python scripts/evaluate.py [--quick]
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.baseline import running_mask  # noqa: E402
from agent.detection import classify_alarm, status_frame  # noqa: E402
from agent.loader import load_context, load_ground_truth  # noqa: E402
from agent.models import MeasurementInput  # noqa: E402
from agent.pipeline import latest_input, run_batch, run_diagnosis  # noqa: E402
from agent.report import output_dir  # noqa: E402

TARGETS = {"top1": 0.80, "top3": 0.95, "accuracy": 0.85, "recall": 0.90, "false_alarm": 0.80}


def event_input(ctx, ev, end=None, drop=()) -> MeasurementInput | None:
    """end(기본: 정비 직전, 미조치는 데이터 끝) 이전 운전 3포인트 평균을 입력으로. drop: 비울 선택 입력."""
    if end is None:
        end = pd.Timestamp(ev["repair_date"]) if pd.notna(ev["repair_date"]) else ctx.data_end + timedelta(hours=2)
    df = ctx.ops[(ctx.ops["pump"] == ev["pump"]) & (ctx.ops["time"] < end) & (ctx.ops["time"] >= pd.Timestamp(ev["start"]))]
    df = df[running_mask(df)].tail(3)
    if df.empty:
        return None
    m = df[["discharge", "flow", "current", "vibration", "suction", "bearing_temp"]].mean()
    inp = MeasurementInput(ev["pump"], *(float(m[k]) for k in m.index),
                           measured_at=(df["time"].iloc[-1] + timedelta(hours=2)).to_pydatetime())
    for k in drop:
        setattr(inp, k, None)
    return inp


def eval_hard(ctx, events):
    """보조 지표: 이른 시점·선택 입력 누락 조건의 원인 적중 (KPI 아님)."""
    out = []
    conds = [("이상징후 시작 +1일 (초기)", "watch_start", timedelta(days=1), ()),
             ("이상 시작 시점", "alarm_start", timedelta(hours=2), ()),
             ("정비 직전, 흡입압력·베어링온도 미입력", None, None, ("suction", "bearing_temp"))]
    for name, col, delta, drop in conds:
        hit = top3 = n = 0
        for _, ev in events.iterrows():
            end = None
            if col:
                if pd.isna(ev[col]):
                    continue
                end = pd.Timestamp(ev[col]) + delta
            inp = event_input(ctx, ev, end, drop)
            if inp is None:
                continue
            modes = [c.mode for c in run_diagnosis(inp, ctx, use_llm=False).candidates]
            n += 1
            hit += bool(modes) and modes[0] == ev["mode"]
            top3 += ev["mode"] in modes
        out.append((name, hit, top3, n))
    return out


def eval_causes(ctx, events):
    rows = []
    for _, ev in events.iterrows():
        inp = event_input(ctx, ev)
        if inp is None:
            continue
        rep = run_diagnosis(inp, ctx, use_llm=False)
        modes = [c.mode for c in rep.candidates]
        rows.append({"event": ev["event_id"], "pump": ev["pump"], "truth": ev["mode"], "status_now": rep.detection.status,
                     "pred1": modes[0] if modes else "-", "top1": bool(modes) and modes[0] == ev["mode"],
                     "top3": ev["mode"] in modes, "ongoing": str(ev["status"]).startswith("미조치"),
                     "priority": rep.priority.level, "conf": rep.candidates[0].confidence if modes else 0})
    return pd.DataFrame(rows)


def eval_status(ctx, labels):
    run = ctx.ops[running_mask(ctx.ops)]
    pred = status_frame(ctx, run)["status"]
    df = run[["pump", "time"]].assign(pred=pred.values).merge(labels[["pump", "time", "label"]], on=["pump", "time"])
    df = df[df["label"] != "정비"]
    acc = (df["pred"] == df["label"]).mean()
    abn = df[df["label"] != "정상"]
    recall = (abn["pred"] != "정상").mean()
    normal = df[df["label"] == "정상"]
    fpr = (normal["pred"] != "정상").mean()
    conf = pd.crosstab(df["label"], df["pred"]).reindex(index=["정상", "이상징후", "이상"], columns=["정상", "이상징후", "이상"], fill_value=0)
    return acc, recall, fpr, conf, len(df)


def eval_false_alarms(ctx, truth_alarms):
    codes = set(ctx.cfg["alarms"]["threshold_codes"])
    a = ctx.alarms[ctx.alarms["code"].isin(codes)].merge(truth_alarms[["alarm_id", "is_false"]], on="alarm_id")
    a["flag"] = [classify_alarm(ctx, r) is not None for _, r in a.iterrows()]
    y = a[a["is_false"] == "Y"]
    n = a[a["is_false"] == "N"]
    return y["flag"].mean(), n["flag"].mean(), len(y), len(n)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="원인 진단만 평가")
    args = ap.parse_args()

    ctx = load_context()
    gt = load_ground_truth()
    _ = ctx.baseline
    lines = [f"# KPI 평가 결과", "", f"데이터: {ctx.source.name}", ""]

    t0 = time.perf_counter()
    causes = eval_causes(ctx, gt["events"])
    done = causes[~causes["ongoing"]]
    ongoing = causes[causes["ongoing"]]
    top1, top3 = done["top1"].mean(), done["top3"].mean()
    ongoing_ok = int(ongoing["top1"].sum())
    results = [("원인 1순위 적중률", top1, TARGETS["top1"], f"{int(done['top1'].sum())}/{len(done)}"),
               ("원인 Top-3 포함률", top3, TARGETS["top3"], f"{int(done['top3'].sum())}/{len(done)}"),
               ("미조치 펌프 1순위 적중", ongoing_ok / max(1, len(ongoing)), 1.0, f"{ongoing_ok}/{len(ongoing)}")]
    print(f"원인 평가 {len(causes)}건: {time.perf_counter() - t0:.1f}s")

    if not args.quick:
        t0 = time.perf_counter()
        acc, recall, fpr, conf, n = eval_status(ctx, gt["labels"])
        print(f"상태 평가 {n:,}행: {time.perf_counter() - t0:.1f}s")
        fa_rec, fa_fpr, ny, nn = eval_false_alarms(ctx, gt["alarms"])
        results += [("상태 판정 정확도", acc, TARGETS["accuracy"], f"{n:,}행"),
                    ("이상(이상징후+이상) 재현율", recall, TARGETS["recall"], f"정상 오탐률 {fpr:.1%}"),
                    ("오경보 식별률", fa_rec, TARGETS["false_alarm"], f"{ny}건 / 실제 알람 오판 {fa_fpr:.1%} ({nn}건 중)")]
        t0 = time.perf_counter()
        for _ in range(3):
            run_diagnosis(latest_input(ctx, "P-105"), ctx, use_llm=False)
        single = (time.perf_counter() - t0) / 3
        t0 = time.perf_counter()
        run_batch(ctx, use_llm=False)
        batch = time.perf_counter() - t0
        results += [("단건 진단 시간(초, LLM 제외)", single, 10.0, "목표 ≤ 10초"),
                    ("12대 일괄 진단 시간(초)", batch, 30.0, "목표 ≤ 30초")]

    lines += ["| 지표 | 결과 | 목표 | 판정 | 비고 |", "|---|---|---|---|---|"]
    for name, val, target, note in results:
        lower_better = "시간" in name
        ok = val <= target if lower_better else val >= target
        shown = f"{val:.2f}" if lower_better else f"{val:.1%}"
        tshown = f"≤ {target:g}" if lower_better else f"≥ {target:.0%}"
        lines.append(f"| {name} | {shown} | {tshown} | {'충족' if ok else '미달'} | {note} |")

    lines += ["", "## 원인 진단 상세", "", "| 이벤트 | 펌프 | 정답 | 1순위 | 적중 | Top-3 | 진단 시점 상태 | 우선순위 |", "|---|---|---|---|---|---|---|---|"]
    for _, r in causes.iterrows():
        lines.append(f"| {r['event']}{' (미조치)' if r['ongoing'] else ''} | {r['pump']} | {r['truth']} | {r['pred1']} | "
                     f"{'O' if r['top1'] else 'X'} | {'O' if r['top3'] else 'X'} | {r['status_now']} | {r['priority']} |")
    by_mode = done.groupby("truth")["top1"].agg(["sum", "count"])
    lines += ["", "## 고장모드별 1순위 적중", "", "| 고장모드 | 적중/건수 |", "|---|---|"]
    lines += [f"| {m} | {int(r['sum'])}/{int(r['count'])} |" for m, r in by_mode.iterrows()]
    wrong = Counter((r["truth"], r["pred1"]) for _, r in done.iterrows() if not r["top1"])
    if wrong:
        lines += ["", "오답 유형: " + ", ".join(f"{t}→{p} {n}건" for (t, p), n in wrong.most_common())]
    hard = eval_hard(ctx, gt["events"])
    lines += ["", "## 보조 지표: 어려운 조건의 원인 진단 (KPI 아님, 전체 이벤트 40건 기준)", "",
              "| 조건 | 1순위 적중 | Top-3 포함 |", "|---|---|---|"]
    lines += [f"| {n} | {h}/{t} ({h / t:.0%}) | {k}/{t} ({k / t:.0%}) |" for n, h, k, t in hard]
    if not args.quick:
        lines += ["", "## 상태 판정 혼동행렬 (행: 정답, 열: 예측)", "", conf.to_markdown() if hasattr(conf, "to_markdown") else conf.to_string()]

    text = "\n".join(lines)
    print(text)
    (output_dir(ctx) / "kpi_report.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
