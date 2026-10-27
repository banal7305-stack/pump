import dataclasses

import pandas as pd
import pytest

from agent.detection import grade_frame
from agent.loader import load_context, parse_arrow, parse_pressure
from agent.models import InputError, MeasurementInput
from agent.pipeline import latest_input, run_batch, run_diagnosis
from agent.report import facts, llm_opinion, save_report, semantic_problems, unsupported_numbers

P105 = dict(pump_id="P-105", discharge=4.47, flow=107.3, current=34.0, vibration=5.61, suction=0.78, bearing_temp=71.7)


@pytest.fixture(scope="module")
def ctx():
    return load_context()


def diag(ctx, **kw):
    args = {**P105, **kw}
    return run_diagnosis(MeasurementInput(**args), ctx, use_llm=False)


# ---------------- 데이터 ----------------
def test_loader_shapes(ctx):
    assert len(ctx.pumps) == 12
    assert len(ctx.ops) == 52_560
    assert len(ctx.modes) == 7


def test_no_ground_truth_in_context(ctx):
    assert "label" not in ctx.ops.columns
    assert "is_false" not in ctx.alarms.columns


def test_kb_parsing():
    assert parse_pressure("↓ (흡입↓↓)") == (-1, -2)
    assert parse_pressure("↑ (토출)") == (1, 0)
    assert parse_pressure("-") == (0, 0)
    assert parse_arrow("↑↑ (점진 상승)") == 2
    assert parse_arrow("↓ 소폭") == -1
    assert parse_arrow("변동") is None


# ---------------- 등급 경계값 (PRD 5.3) ----------------
@pytest.mark.parametrize("var,value,grade", [
    ("vibration", 2.8, 0), ("vibration", 4.5, 1), ("vibration", 4.51, 2), ("vibration", 7.1, 2), ("vibration", 7.11, 3),
    ("bearing_temp", 65, 0), ("bearing_temp", 70, 1), ("bearing_temp", 70.1, 2), ("bearing_temp", 80, 2), ("bearing_temp", 80.1, 3),
    ("current", 38.0, 0), ("current", 39.9, 1), ("current", 40.0, 2), ("current", 43.7, 2), ("current", 43.8, 3),
    ("flow", 108.0, 0), ("flow", 102.0, 1), ("flow", 101.9, 2),
    ("discharge", 4.275, 0), ("discharge", 3.96, 1), ("discharge", 3.95, 2), ("discharge", 4.95, 1), ("discharge", 4.96, 2),
    ("suction", 0.64, 0), ("suction", 0.56, 1), ("suction", 0.48, 2), ("suction", 0.47, 3),
])
def test_grade_boundaries(ctx, var, value, grade):
    # P-101: 정격 유량 120, 토출 4.5, 흡입 0.8, 전류 38
    rated = ctx.pump("P-101").rated
    raw = pd.DataFrame([{"pump": "P-101", "vibration": 1.0, "bearing_temp": 50.0, "current": 30.0,
                         "flow": 110.0, "discharge": 4.4, "suction": 0.8}])
    raw[var] = value
    model = raw.copy()
    for v in rated:
        model[v] = raw[v] / rated[v]
    assert grade_frame(ctx.cfg, raw, model).iloc[0][var] == grade


# ---------------- 입력 검증 ----------------
@pytest.mark.parametrize("kw,field", [
    ({"pump_id": "P-999"}, "pump_id"), ({"flow": -5}, "flow"), ({"vibration": 99}, "vibration"),
    ({"flow": 0, "current": 0}, "flow"), ({"discharge": None}, "discharge"), ({"current": "abc"}, "current"),
])
def test_invalid_inputs(ctx, kw, field):
    with pytest.raises(InputError) as e:
        diag(ctx, **kw)
    assert e.value.field == field


def test_unknown_sign(ctx):
    with pytest.raises(InputError):
        diag(ctx, signs=["연기 발생"])


# ---------------- 진단 결과 ----------------
def test_p105_example(ctx):
    rep = diag(ctx)
    assert rep.detection.status == "이상"
    assert rep.candidates[0].mode == "베어링 열화"
    assert rep.priority.level in ("긴급", "즉시")
    assert rep.detection.grade_of("vibration").grade_name == "주의"
    assert rep.detection.trend.available


@pytest.mark.parametrize("pump,mode", [("P-105", "베어링 열화"), ("P-109", "캐비테이션/임펠러 마모"), ("P-112", "축정렬 불량")])
def test_unresolved_pumps(ctx, pump, mode):
    rep = run_diagnosis(latest_input(ctx, pump), ctx, use_llm=False)
    assert rep.candidates and rep.candidates[0].mode == mode


def test_normal_pump_has_no_candidates(ctx):
    rep = run_diagnosis(latest_input(ctx, "P-106"), ctx, use_llm=False)
    assert rep.detection.status == "정상"
    assert rep.candidates == []
    assert rep.priority.level == "관찰"


def test_missing_optional_inputs(ctx):
    full = diag(ctx)
    rep = diag(ctx, suction=None, bearing_temp=None)
    assert rep.checks[0].reason == "미입력 항목"
    assert "흡입압력" in rep.checks[0].item or "베어링온도" in rep.checks[0].item
    assert rep.candidates[0].confidence < full.candidates[0].confidence


def test_field_sign_raises_matching_cause(ctx):
    base = {c.mode: c.score for c in diag(ctx).candidates}
    signed = {c.mode: c.score for c in diag(ctx, signs=["커플링 발열"]).candidates}
    assert signed["축정렬 불량"] > base["축정렬 불량"]


def test_spike_input_flagged(ctx):
    inp = latest_input(ctx, "P-106")
    inp = dataclasses.replace(inp, vibration=6.0)
    rep = run_diagnosis(inp, ctx, use_llm=False)
    assert rep.detection.false_alarm_note


def test_deterministic(ctx):
    a, b = diag(ctx), diag(ctx)
    assert [(c.mode, c.score) for c in a.candidates] == [(c.mode, c.score) for c in b.candidates]
    assert a.priority.score == b.priority.score


def test_history_is_time_bounded(ctx):
    """측정 시각 이후의 정비이력·알람을 쓰지 않는다 (P-101 베어링 교체 WO 이전 시점)."""
    rep = diag(ctx, pump_id="P-101", measured_at=pd.Timestamp("2026-08-10 00:00").to_pydatetime())
    for c in rep.candidates:
        for s in c.similar_cases:
            assert s.date < pd.Timestamp("2026-08-10")


def test_batch(ctx):
    reps = run_batch(ctx)
    assert len(reps) == 12
    scores = [r.priority.score for r in reps]
    assert scores == sorted(scores, reverse=True)


# ---------------- 보고서 ----------------
def test_save_report(ctx, tmp_path, monkeypatch):
    monkeypatch.setitem(ctx.cfg["data"], "output_dir", str(tmp_path))
    paths = save_report(ctx, diag(ctx))
    md = paths["md"].read_text(encoding="utf-8")
    for i in range(1, 9):
        assert f"## {i}." in md
    assert ctx.cfg["report"]["disclaimer"] in md
    assert paths["docx"].stat().st_size > 0 and paths["xlsx"].stat().st_size > 0


def test_llm_skipped_without_credentials(ctx, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert llm_opinion(ctx, diag(ctx)) is None


def test_llm_number_guard(ctx):
    data = facts(diag(ctx))
    ok = "P-105는 진동 5.61 mm/s(약 5.6)로 2026-10-01 00:00 측정 기준 1순위 원인 베어링 열화입니다."
    assert unsupported_numbers(ok, data) == []
    # 펌프ID·날짜 속 숫자, 부호를 뒤집은 값, 없는 식별자는 거부
    assert "105" in unsupported_numbers("진동이 105 mm/s입니다.", data)
    assert "2026" in unsupported_numbers("2026 mm/s까지 오릅니다.", data)
    assert "-5.61" in unsupported_numbers("진동 -5.61 감소", data)
    assert "WO-9999" in unsupported_numbers("WO-9999 사례 참고", data)
    assert unsupported_numbers("진동 5.610 mm/s", data) == []      # 반올림 표기 허용


def test_llm_semantic_guard(ctx):
    data = facts(diag(ctx))
    good = "종합 상태는 '이상'이며 베어링 열화가 유력하고 우선순위는 '긴급'입니다."
    assert semantic_problems(good, data) == []
    assert semantic_problems("종합 상태는 '정상'이며 베어링 열화, 우선순위는 '긴급'입니다.", data)
    assert semantic_problems("상태는 이상이고 축정렬 불량입니다. 우선순위는 긴급.", data)   # 1순위 원인 누락
    assert semantic_problems("상태는 이상, 베어링 열화. 우선순위는 '관찰'입니다.", data)


class _FakeResp:
    def __init__(self, text, stop="end_turn"):
        self.stop_reason = stop
        self.content = [type("B", (), {"type": "text", "text": text})()]


def _fake_client(monkeypatch, resp, calls):
    import anthropic

    class Fake:
        def __init__(self, **kw):
            self.beta = self
            self.messages = self

        def create(self, **kw):
            calls.append(kw)
            return resp
    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


@pytest.mark.parametrize("text,stop,accepted", [
    ("P-105 종합 상태는 '이상'이며 진동 5.61 mm/s로 베어링 열화가 유력합니다. 우선순위는 '긴급'입니다.", "end_turn", True),
    ("종합 상태는 '이상', 진동이 9.87 mm/s, 베어링 열화, 우선순위 '긴급'.", "end_turn", False),   # 없는 숫자
    ("종합 상태는 '이상', 베어링 열화, 우선순위 '긴급'", "max_tokens", False),                   # 잘린 응답
    ("", "refusal", False),
])
def test_llm_opinion_guarded(ctx, monkeypatch, text, stop, accepted):
    calls = []
    _fake_client(monkeypatch, _FakeResp(text, stop), calls)
    rep = diag(ctx)
    out = llm_opinion(ctx, rep)
    assert (out == text) if accepted else (out is None)
    kw = calls[0]
    assert kw["model"] == ctx.cfg["llm"]["model"]
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]


# ---------------- 4단계 수정 회귀 테스트 ----------------
def test_alarm_classification_uses_only_past(ctx):
    """B2: 측정 시각 이후 포인트로 오경보를 판정하지 않는다."""
    from agent.detection import classify_alarm
    a = ctx.alarms[ctx.alarms["code"].isin(ctx.cfg["alarms"]["threshold_codes"])]
    flagged = [r for _, r in a.iterrows() if classify_alarm(ctx, r) and "즉시 복귀" in classify_alarm(ctx, r)]
    assert flagged
    r = flagged[0]
    # 알람 직후(다음 포인트 이전) 측정이면 판단 보류
    assert classify_alarm(ctx, r, before=r["time"] + pd.Timedelta(minutes=1)) is None


@pytest.mark.parametrize("kw,field", [
    ({"suction": -1000}, "suction"), ({"vibration": True}, "vibration"),
    ({"measured_at": pd.Timestamp("2020-01-01").to_pydatetime()}, "measured_at"),
    ({"measured_at": pd.Timestamp("2030-01-01").to_pydatetime()}, "measured_at"),
])
def test_validation_fixes(ctx, kw, field):
    with pytest.raises(InputError) as e:
        diag(ctx, **kw)
    assert e.value.field == field


def test_timezone_aware_time_accepted(ctx):
    rep = diag(ctx, measured_at=pd.Timestamp("2026-09-30 20:00", tz="Asia/Seoul").to_pydatetime())
    assert rep.measured_at.tzinfo is None


def test_missing_signs_check_item(ctx):
    rep = diag(ctx, pump_id="P-112", **{k: v for k, v in latest_input(ctx, "P-112").values().items()},
               measured_at=pd.Timestamp("2026-09-26 00:00").to_pydatetime())
    reasons = [(c.item, c.reason) for c in rep.checks]
    if not rep.detection.field_signs:
        assert any("현장 징후" in i and r == "미입력 항목" for i, r in reasons)
