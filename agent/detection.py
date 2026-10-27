"""F1-6 입력 검증, F2 이상 탐지 (등급·기준선 z-score·추세·알람 맥락·오경보)."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd

from .baseline import anomaly_index, running_mask, to_model_space
from .loader import DataContext
from .models import (VARIABLES, AlarmSummary, DetectionResult, InputError, ItemGrade,
                     MeasurementInput, Trend, LABELS)


# ---------------- 입력 검증 ----------------
def validate_input(ctx: DataContext, inp: MeasurementInput) -> None:
    if inp.pump_id not in ctx.pumps:
        raise InputError(f"없는 펌프ID입니다: {inp.pump_id} (사용 가능: {', '.join(ctx.pumps)})", "pump_id")
    vcfg = ctx.cfg["validation"]["max"]
    rated = ctx.pump(inp.pump_id).rated
    for v, val in inp.values().items():
        if val is None:
            if v in ("suction", "bearing_temp"):
                continue
            raise InputError(f"{LABELS[v]} 값을 입력하세요.", v)
        if isinstance(val, bool):
            raise InputError(f"{LABELS[v]} 값이 숫자가 아닙니다: {val!r}", v)
        try:
            val = float(val)
        except (TypeError, ValueError):
            raise InputError(f"{LABELS[v]} 값이 숫자가 아닙니다: {val!r}", v)
        if not np.isfinite(val):
            raise InputError(f"{LABELS[v]} 값이 올바르지 않습니다.", v)
        low = ctx.cfg["validation"].get("min", {}).get(v)
        if low is not None and val < low:
            raise InputError(f"{LABELS[v]} 값이 현실적인 범위를 벗어났습니다: {val} (하한 {low}). 단위를 확인하세요.", v)
        if val < 0 and v != "suction":
            raise InputError(f"{LABELS[v]} 값은 음수일 수 없습니다: {val}", v)
        limit = vcfg.get(v) if v in ("vibration", "bearing_temp") else vcfg.get(f"{v}_ratio")
        check = val if v in ("vibration", "bearing_temp") else val / rated[v]
        if limit is not None and check > limit:
            shown = f"{limit}" if v in ("vibration", "bearing_temp") else f"정격의 {limit * 100:.0f}%"
            raise InputError(f"{LABELS[v]} 값이 현실적인 범위를 벗어났습니다: {val} (상한 {shown}). 단위를 확인하세요.", v)
        setattr(inp, v, val)
    unknown = [s for s in inp.signs if s not in ctx.cfg["field_signs"]]
    if unknown:
        raise InputError(f"알 수 없는 현장 징후: {unknown} (사용 가능: {ctx.cfg['field_signs']})", "signs")


def is_stopped(ctx: DataContext, inp: MeasurementInput) -> bool:
    vcfg = ctx.cfg["validation"]
    rated = ctx.pump(inp.pump_id).rated
    return (inp.flow / rated["flow"] < vcfg["stopped_flow_ratio"]
            and inp.current / rated["current"] < vcfg["stopped_current_ratio"])


# ---------------- 등급·상태 (벡터 연산: 단건·전수 평가 공용) ----------------
def grade_frame(cfg: dict, raw: pd.DataFrame, model: pd.DataFrame) -> pd.DataFrame:
    """항목별 등급 (-1 미입력, 0~3). discharge 는 저하·상승 중 큰 쪽."""
    out = {}
    for key, g in cfg["grades"].items():
        src = g.get("source", key)
        vals = (model[src] if g["ratio"] else raw[src]).round(4)
        grade = pd.Series(0, index=raw.index)
        for b in g["bounds"]:
            grade += (vals > b) if g["direction"] == "high" else (vals < b)
        grade[vals.isna()] = -1
        out[key] = grade
    df = pd.DataFrame(out)
    df["discharge"] = df[["discharge", "discharge_high"]].max(axis=1)
    return df.drop(columns="discharge_high")


def status_frame(ctx: DataContext, df: pd.DataFrame) -> pd.DataFrame:
    """df(pump, time, 측정값) → 등급, z, 이상지수, 종합 상태."""
    scfg = ctx.cfg["status"]
    model, expected, z = ctx.baseline.zscores(df)
    grades = grade_frame(ctx.cfg, df, model)
    max_grade = grades.max(axis=1)
    d = anomaly_index(z, scfg["deadzone"])
    status = np.where((d >= scfg["alarm_threshold"]) | (max_grade >= scfg["grade_alarm"]), "이상",
                      np.where((d >= scfg["watch_threshold"]) | (max_grade >= scfg["grade_watch"]), "이상징후", "정상"))
    out = pd.concat([grades.add_prefix("g_"), z.add_prefix("z_"), model.add_prefix("m_"),
                     expected.add_prefix("e_")], axis=1)
    out["max_grade"] = max_grade
    out["anomaly_index"] = d
    out["status"] = status
    return out


# ---------------- 추세 ----------------
def trend_for(ctx: DataContext, pump_id: str, time: pd.Timestamp, current_model: pd.Series,
              current_z: pd.Series | None = None) -> Trend:
    """진행 속도 점수는 기준선에서 벗어난 항목(|z| ≥ status.deadzone)의 악화 속도만 반영한다."""
    tcfg = ctx.cfg["trend"]
    hist = ctx.history(pump_id, time)
    hist = hist[running_mask(hist) & (hist["time"] >= time - timedelta(days=tcfg["window_days"]))]
    if len(hist) < tcfg["min_points"]:
        return Trend(False, len(hist), speed_score=float(ctx.cfg["priority"]["neutral_speed"]),
                     note=f"최근 {tcfg['window_days']}일 운전 이력이 {len(hist)}건뿐이라 추세를 판단할 수 없습니다.")
    m = to_model_space(hist, ctx.baseline.rated)
    days = ((hist["time"] - time).dt.total_seconds() / 86400).to_numpy()
    days = np.append(days, 0.0)
    slopes, eta, scores = {}, {}, []
    for v in VARIABLES:
        y = np.append(m[v].to_numpy(dtype=float), current_model.get(v, np.nan))
        ok = ~np.isnan(y)
        if ok.sum() < tcfg["min_points"]:
            continue
        slope = float(np.polyfit(days[ok], y[ok], 1)[0])
        slopes[v] = slope
        z = current_z.get(v, np.nan) if current_z is not None else np.inf
        if not (abs(z) >= ctx.cfg["status"]["deadzone"]):
            continue
        # 기준선에서 벗어난 방향으로 더 벌어지는 속도만 악화로 본다
        worsening = slope * np.sign(z) if np.isfinite(z) else abs(slope)
        scores.append(min(100.0, max(0.0, 100 * worsening / tcfg["speed_unit"][v])))
    for v, warn in (("vibration", tcfg["warn_vibration"]), ("bearing_temp", tcfg["warn_bearing_temp"])):
        now = current_model.get(v, np.nan)
        s = slopes.get(v)
        if s is None or np.isnan(now):
            continue
        if now > warn:
            eta[v] = 0.0
        elif s > 1e-6:
            eta[v] = float((warn - now) / s)
        else:
            eta[v] = None
    for days_lim, score in tcfg["eta_scores"]:
        if any(e is not None and e <= days_lim for e in eta.values()):
            scores.append(float(score))
            break
    return Trend(True, len(hist), slopes, eta, float(max(scores) if scores else 0.0))


# ---------------- 오경보 ----------------
def _jump_check(cfg: dict, prev: pd.DataFrame, x: pd.Series, variables) -> list[str]:
    """직전 N개 운전 포인트 중앙값 대비 급변한 항목."""
    if prev.empty:
        return []
    med = prev[variables].median()
    out = []
    for v in variables:
        lim = cfg["false_alarm"]["jump"].get(v)
        if lim is not None and pd.notna(x.get(v)) and pd.notna(med.get(v)) and abs(x[v] - med[v]) > lim:
            out.append(v)
    return out


def classify_alarm(ctx: DataContext, alarm: pd.Series, before: Optional[pd.Timestamp] = None) -> Optional[str]:
    """과거 알람의 오경보 가능성. 사유 문자열 또는 None.

    알람 다음 포인트로 '즉시 복귀'를 확인하므로, before(측정 시각)를 주면 그 이전 데이터만 쓴다.
    다음 포인트가 측정 시각 이전에 없으면 판단을 보류(None)한다. before=None은 사후 평가용.
    """
    code_var = ctx.cfg["alarms"]["code_variable"]
    var = code_var.get(alarm["code"])
    if var is None:
        return None
    df = ctx.ops[ctx.ops["pump"] == alarm["pump"]]
    if before is not None:
        df = df[df["time"] < before]
    idx = df.index[df["time"] <= alarm["time"]]
    if len(idx) == 0:
        return None
    i = idx[-1]
    pos = df.index.get_loc(i)
    k = ctx.cfg["false_alarm"]["history_points"]
    before = df.iloc[max(0, pos - ctx.cfg["false_alarm"]["restart_points"]):pos]
    if len(before) and not running_mask(before).all():
        return "정지 후 재기동 직후 발생 (과도 구간)"
    model = to_model_space(df.iloc[max(0, pos - k): pos + 2], ctx.baseline.rated)
    run = running_mask(df.iloc[max(0, pos - k): pos + 2])
    model = model[run]
    if i not in model.index:
        return None
    prev = model.loc[model.index < i].tail(k)
    nxt = model.loc[model.index > i].head(1)
    jumped = _jump_check(ctx.cfg, prev, model.loc[i], [var])
    if not jumped or nxt.empty:
        return None
    lim = ctx.cfg["false_alarm"]["jump"][var]
    if abs(nxt.iloc[0][var] - prev[var].median()) <= lim / 2:
        return f"{LABELS[var]} 단발성 급변 후 즉시 복귀 (계측 노이즈·순간 부하 변동)"
    return None


# ---------------- 알람 맥락 ----------------
def field_signs_from_alarms(ctx: DataContext, alarms: pd.DataFrame) -> dict[str, str]:
    out = {}
    for rule in ctx.cfg["alarms"]["field_signs"]:
        hit = alarms[(alarms["code"] == rule["code"]) & alarms["name"].astype(str).str.contains(rule["keyword"])]
        if len(hit):
            last = hit.iloc[-1]
            out[rule["sign"]] = f"{last['alarm_id']} ({last['time']:%m-%d %H:%M} {last['name']})"
    return out


def alarm_context(ctx: DataContext, pump_id: str, time: pd.Timestamp) -> tuple[list[AlarmSummary], pd.DataFrame]:
    acfg = ctx.cfg["alarms"]
    a = ctx.alarms[(ctx.alarms["pump"] == pump_id) & (ctx.alarms["time"] < time)
                   & (ctx.alarms["time"] >= time - timedelta(days=acfg["context_days"]))].sort_values("time")
    out = []
    for code, g in a.groupby("code", sort=False):
        dates = sorted(set(g["time"].dt.date))
        consec = 1
        for d0, d1 in zip(dates[::-1][1:], dates[::-1]):
            if (d1 - d0).days == 1:
                consec += 1
            else:
                break
        vals = [float(v) for v in g["value"] if pd.notna(v)]
        false_n = (sum(classify_alarm(ctx, r, before=time) is not None for _, r in g.iterrows())
                   if code in acfg["threshold_codes"] else 0)
        out.append(AlarmSummary(code, str(g["name"].iloc[-1]), len(g), consec, g["time"].iloc[-1].to_pydatetime(),
                                vals[-6:], len(vals) >= 2 and vals[-1] > vals[0], false_n))
    out.sort(key=lambda s: s.last_time, reverse=True)
    return out, a


# ---------------- 단건 탐지 ----------------
def input_frame(inp: MeasurementInput, time: pd.Timestamp) -> pd.DataFrame:
    row = {"time": time, "pump": inp.pump_id, "state": "운전"}
    row.update({v: (np.nan if val is None else float(val)) for v, val in inp.values().items()})
    return pd.DataFrame([row])


def detect(ctx: DataContext, inp: MeasurementInput, time: pd.Timestamp) -> DetectionResult:
    df = input_frame(inp, time)
    s = status_frame(ctx, df).iloc[0]
    grades = []
    for v in VARIABLES:
        val = inp.values()[v]
        ratio = s[f"m_{v}"] if v in ctx.baseline.rated.columns and val is not None else None
        z = s[f"z_{v}"]
        grades.append(ItemGrade(v, val, None if ratio is None else float(ratio), int(s[f"g_{v}"]),
                                None if pd.isna(z) else float(z),
                                ctx.baseline.to_value(inp.pump_id, v, s[f"e_{v}"]) if pd.notna(s[f"e_{v}"]) else None))
    model_now = pd.Series({v: s[f"m_{v}"] for v in VARIABLES})
    z_now = pd.Series({v: s[f"z_{v}"] for v in VARIABLES})
    trend = trend_for(ctx, inp.pump_id, time, model_now, z_now)
    alarms, recent = alarm_context(ctx, inp.pump_id, time)

    ev = recent[recent["time"] >= time - timedelta(days=ctx.cfg["alarms"]["evidence_days"])]
    sources = {sg: "입력" for sg in inp.signs}
    for sg, src in field_signs_from_alarms(ctx, ev).items():
        sources.setdefault(sg, f"현장 알람 {src}")

    # 입력값 자체의 오경보 가능성 (직전 이력 대비 급변, 재기동 직후)
    note = None
    hist = ctx.history(inp.pump_id, time).tail(ctx.cfg["false_alarm"]["history_points"])
    if len(hist):
        if not running_mask(hist.tail(ctx.cfg["false_alarm"]["restart_points"])).all():
            note = "정지 직후 재기동 구간의 값일 수 있습니다. 안정화 후 재측정을 권장합니다."
        else:
            prev = to_model_space(hist[running_mask(hist)], ctx.baseline.rated)
            jumped = [v for v in _jump_check(ctx.cfg, prev, model_now, VARIABLES) if s[f"g_{v}"] >= 2]
            if jumped:
                names = ", ".join(LABELS[v] for v in jumped)
                note = f"{names}이(가) 직전 이력 대비 급변했습니다. 계측 오류·순간 부하 변동(오경보) 가능성이 있어 재측정을 권장합니다."

    return DetectionResult(grades=grades, max_grade=int(s["max_grade"]), anomaly_index=float(s["anomaly_index"]),
                           status=str(s["status"]), trend=trend, alarms=alarms, field_signs=list(sources),
                           sign_sources=sources, false_alarm_note=note)
