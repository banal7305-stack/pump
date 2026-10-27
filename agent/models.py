"""입력·결과 데이터 클래스."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

# 내부 변수명 ↔ 한글 표시명
VARIABLES = ["discharge", "flow", "current", "vibration", "suction", "bearing_temp"]
LABELS = {
    "discharge": "토출압력", "flow": "유량", "current": "전류", "vibration": "진동RMS",
    "suction": "흡입압력", "bearing_temp": "베어링온도", "discharge_high": "토출압력 상승",
}
UNITS = {"discharge": "bar", "flow": "m³/h", "current": "A", "vibration": "mm/s",
         "suction": "bar", "bearing_temp": "℃"}
RATIO_VARS = {"discharge", "flow", "current", "suction"}   # 정격 대비 비율로 보는 항목
GRADE_NAMES = ["정상", "관찰", "주의", "경고"]
STATUSES = ["정상", "이상징후", "이상"]


class InputError(ValueError):
    """사용자 입력 오류. field 는 문제가 된 입력 항목."""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field


@dataclass
class MeasurementInput:
    pump_id: str
    discharge: float
    flow: float
    current: float
    vibration: float
    suction: Optional[float] = None
    bearing_temp: Optional[float] = None
    measured_at: Optional[datetime] = None
    signs: list[str] = field(default_factory=list)

    def values(self) -> dict[str, Optional[float]]:
        return {v: getattr(self, v) for v in VARIABLES}

    def missing(self) -> list[str]:
        return [v for v in ("suction", "bearing_temp") if getattr(self, v) is None]


@dataclass
class ItemGrade:
    variable: str
    value: Optional[float]
    ratio: Optional[float]        # 정격 대비 (비율 항목만)
    grade: int                    # 0~3, 미입력이면 -1
    z: Optional[float]            # 기준선 대비 z-score
    baseline: Optional[float]

    @property
    def label(self) -> str:
        return LABELS[self.variable]

    @property
    def grade_name(self) -> str:
        return "미입력" if self.grade < 0 else GRADE_NAMES[self.grade]


@dataclass
class Trend:
    available: bool
    points: int
    slopes: dict[str, float] = field(default_factory=dict)        # 일 변화량 (비율 항목은 정격 대비)
    eta_days: dict[str, Optional[float]] = field(default_factory=dict)  # 경고 도달 예상일
    speed_score: float = 50.0
    note: str = ""


@dataclass
class AlarmSummary:
    code: str
    name: str
    count: int
    consecutive_days: int
    last_time: datetime
    values: list[float]
    rising: bool
    suspected_false: int          # 오경보 가능성으로 본 건수


@dataclass
class DetectionResult:
    grades: list[ItemGrade]
    max_grade: int
    anomaly_index: float
    status: str
    trend: Trend
    alarms: list[AlarmSummary]
    field_signs: list[str]                       # 입력 + 최근 현장 알람
    sign_sources: dict[str, str]
    false_alarm_note: Optional[str] = None
    stopped: bool = False

    def grade_of(self, variable: str) -> Optional[ItemGrade]:
        return next((g for g in self.grades if g.variable == variable), None)


@dataclass
class SimilarCase:
    wo_id: str
    date: datetime
    pump_id: str
    part: str
    cause: str
    symptom: str
    action: str
    hours: float
    cost: int


@dataclass
class CauseCandidate:
    mode: str
    score: float                  # 0~1 (순위용)
    confidence: float             # 0~100 (누락 입력 반영)
    confidence_label: str
    evidence: list[str]
    mismatches: list[str]
    similar_cases: list[SimilarCase] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class CheckItem:
    order: int
    item: str
    reason: str


@dataclass
class PriorityResult:
    score: float
    level: str
    deadline: str
    components: dict[str, float]
    work_steps: list[str]
    parts: str
    avg_hours: Optional[float]
    avg_cost: Optional[float]
    case_count: int


@dataclass
class PumpInfo:
    pump_id: str
    name: str
    location: str
    pump_type: str
    kw: float
    rated: dict[str, float]       # discharge, flow, current, suction
    installed: Optional[datetime]
    importance: float


@dataclass
class DiagnosisReport:
    pump: PumpInfo
    input: MeasurementInput
    measured_at: datetime
    detection: DetectionResult
    candidates: list[CauseCandidate]
    checks: list[CheckItem]
    priority: PriorityResult
    opinion: str = ""
    opinion_source: str = "template"   # template | llm

    def to_dict(self) -> dict:
        return asdict(self)
