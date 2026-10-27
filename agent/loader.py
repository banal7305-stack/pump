"""엑셀 데이터 로드, 캐시, 펌프 정보·지식베이스 파싱.

진단 로직에는 정답 열(상태라벨(정답), 오경보(정답), 이상이벤트(정답) 시트)을 넘기지 않는다.
정답은 load_ground_truth()로만 꺼내며 scripts/evaluate.py 채점에서만 쓴다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .models import PumpInfo

ROOT = Path(__file__).resolve().parent.parent

SHEETS = {
    "master": ("설비마스터", {
        "펌프ID": "pump", "설비명": "name", "위치": "location", "형식": "pump_type", "모터(kW)": "kw",
        "정격유량(m³/h)": "rated_flow", "정격토출압력(bar)": "rated_discharge",
        "정격흡입압력(bar)": "rated_suction", "정격전류(A)": "rated_current", "설치일": "installed",
        "설비중요도(점수)": "importance"}),
    "ops": ("운전데이터", {
        "일시": "time", "펌프ID": "pump", "흡입압력(bar)": "suction", "토출압력(bar)": "discharge",
        "유량(m³/h)": "flow", "전류(A)": "current", "진동RMS(mm/s)": "vibration",
        "베어링온도(℃)": "bearing_temp", "운전상태": "state", "상태라벨(정답)": "label"}),
    "alarms": ("알람이력", {
        "알람ID": "alarm_id", "발생일시": "time", "펌프ID": "pump", "알람코드": "code", "알람명": "name",
        "등급": "grade", "측정값": "value", "임계값": "threshold", "해제일시": "cleared", "비고": "note",
        "오경보(정답)": "is_false"}),
    "work_orders": ("정비이력", {
        "작업ID": "wo_id", "작업일자": "date", "펌프ID": "pump", "작업유형": "work_type", "대상부품": "part",
        "고장원인(확인)": "cause", "작업 전 증상": "symptom", "조치내용": "action", "작업시간(h)": "hours",
        "비용(원)": "cost", "비고": "note"}),
    "events": ("이상이벤트(정답)", {
        "이벤트ID": "event_id", "펌프ID": "pump", "고장모드": "mode", "근본원인": "root_cause",
        "시작일시": "start", "이상징후 시작": "watch_start", "이상 시작": "alarm_start",
        "정비일자": "repair_date", "작업ID": "wo_id", "최대 진행률": "max_progress", "상태": "status"}),
    "kb": ("고장모드_지식베이스", {
        "고장모드": "mode", "진동": "vibration", "전류": "current", "압력": "pressure", "유량": "flow",
        "베어링온도": "bearing_temp", "현장 징후": "field_sign", "추가 확인 항목": "checks",
        "권장 점검 순서": "steps"}),
}
GROUND_TRUTH_COLUMNS = {"ops": ["label"], "alarms": ["is_false"]}
GROUND_TRUTH_SHEETS = {"events"}


class DataError(RuntimeError):
    pass


def load_config(path: Optional[Path] = None) -> dict:
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


def find_data_file(cfg: dict, override: Optional[str | Path] = None) -> Path:
    """설정된 후보 중 필요한 시트를 모두 가진 첫 엑셀 파일을 고른다."""
    candidates = [_resolve(override)] if override else [_resolve(p) for p in cfg["data"]["paths"]]
    need = {sheet for sheet, _ in SHEETS.values()}
    problems = []
    for path in candidates:
        if not path.exists():
            problems.append(f"{path.name}: 파일 없음")
            continue
        try:
            names = set(pd.ExcelFile(path).sheet_names)
        except Exception as e:  # 손상·잠금 등
            problems.append(f"{path.name}: 열 수 없음 ({e})")
            continue
        missing = need - names
        if missing:
            problems.append(f"{path.name}: 시트 없음 {sorted(missing)}")
            continue
        return path
    raise DataError("사용할 수 있는 데이터 파일이 없습니다.\n  " + "\n  ".join(problems))


def _read_sheets(path: Path) -> dict[str, pd.DataFrame]:
    raw = pd.read_excel(path, sheet_name=[s for s, _ in SHEETS.values()])
    out = {}
    for key, (sheet, cols) in SHEETS.items():
        df = raw[sheet]
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise DataError(f"'{sheet}' 시트에 컬럼이 없습니다: {missing}")
        out[key] = df[list(cols)].rename(columns=cols)
    return out


def _load_tables(path: Path, cache_dir: Path) -> dict[str, pd.DataFrame]:
    """엑셀은 느리므로(5만 행) parquet 캐시를 쓴다. 원본 파일이 바뀌면 다시 만든다."""
    stat = path.stat()
    stamp = {"source": str(path), "mtime": stat.st_mtime, "size": stat.st_size}
    meta = cache_dir / "meta.json"
    if meta.exists():
        try:
            if json.loads(meta.read_text(encoding="utf-8")) == stamp:
                return {k: pd.read_parquet(cache_dir / f"{k}.parquet") for k in SHEETS}
        except Exception:
            pass  # 캐시가 깨졌으면 새로 만든다
    tables = _read_sheets(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for k, df in tables.items():
        df.to_parquet(cache_dir / f"{k}.parquet", index=False)
    meta.write_text(json.dumps(stamp), encoding="utf-8")
    return tables


# ---------------- 지식베이스 파싱 ----------------
def parse_arrow(text) -> Optional[int]:
    """'↑↑ (점진 상승)' → 2, '↓ 소폭' → -1, '-' → 0, '변동' → None"""
    s = str(text).strip() if text is not None and not pd.isna(text) else "-"
    if s.startswith("↑↑"):
        return 2
    if s.startswith("↑"):
        return 1
    if s.startswith("↓↓"):
        return -2
    if s.startswith("↓"):
        return -1
    if s.startswith("변동"):
        return None
    return 0


def parse_pressure(text) -> tuple[Optional[int], Optional[int]]:
    """압력 열 → (토출, 흡입). '↓ (흡입↓↓)' → (-1, -2), '↑ (토출)' → (1, 0)"""
    s = str(text).strip() if text is not None and not pd.isna(text) else "-"
    main = parse_arrow(s.split("(")[0])
    suction = 0
    m = re.search(r"\(([^)]*)\)", s)
    if m and "흡입" in m.group(1):
        suction = parse_arrow(m.group(1).split("흡입", 1)[1])
    return main, suction


@dataclass
class FailureMode:
    mode: str
    expected: dict[str, Optional[int]]   # 변수 → 기대 방향(-2~2), None은 비교 제외
    field_sign: str
    checks: list[str]
    steps: list[str]


def parse_kb(kb: pd.DataFrame) -> list[FailureMode]:
    modes = []
    for _, r in kb.iterrows():
        dis, suc = parse_pressure(r["pressure"])
        expected = {"vibration": parse_arrow(r["vibration"]), "current": parse_arrow(r["current"]),
                    "discharge": dis, "suction": suc, "flow": parse_arrow(r["flow"]),
                    "bearing_temp": parse_arrow(r["bearing_temp"])}
        checks = [c.strip() for c in str(r["checks"]).split(",") if c.strip() and c.strip() != "-"]
        steps = [re.sub(r"^\d+\.\s*", "", s.strip()) for s in str(r["steps"]).split("→") if s.strip()]
        modes.append(FailureMode(str(r["mode"]).strip(), expected, str(r["field_sign"]).strip(), checks, steps))
    return modes


# ---------------- 컨텍스트 ----------------
@dataclass
class DataContext:
    cfg: dict
    source: Path
    master: pd.DataFrame
    ops: pd.DataFrame
    alarms: pd.DataFrame
    work_orders: pd.DataFrame
    modes: list[FailureMode]
    pumps: dict[str, PumpInfo] = field(default_factory=dict)
    _baseline: object = None

    @property
    def baseline(self):
        if self._baseline is None:
            from .baseline import BaselineModel
            self._baseline = BaselineModel.fit(self)
        return self._baseline

    def pump(self, pump_id: str) -> PumpInfo:
        return self.pumps[pump_id]

    def mode(self, name: str) -> FailureMode:
        return next(m for m in self.modes if m.mode == name)

    def history(self, pump_id: str, before) -> pd.DataFrame:
        """해당 펌프의 측정 시각 이전 운전데이터 (시간순)."""
        df = self.ops[self.ops["pump"] == pump_id]
        return df[df["time"] < before] if before is not None else df

    @property
    def data_end(self) -> pd.Timestamp:
        return self.ops["time"].max()


def _pump_infos(master: pd.DataFrame, cfg: dict) -> dict[str, PumpInfo]:
    out = {}
    for _, r in master.iterrows():
        bad = [c for c in ("rated_flow", "rated_discharge", "rated_suction", "rated_current")
               if not (pd.notna(r[c]) and float(r[c]) > 0)]
        if bad:
            raise DataError(f"설비마스터 {r['pump']}의 정격값이 비었거나 0입니다: {bad}")
        imp = r.get("importance")
        out[r["pump"]] = PumpInfo(
            pump_id=r["pump"], name=r["name"], location=r["location"], pump_type=r["pump_type"],
            kw=float(r["kw"]),
            rated={"flow": float(r["rated_flow"]), "discharge": float(r["rated_discharge"]),
                   "suction": float(r["rated_suction"]), "current": float(r["rated_current"])},
            installed=r["installed"] if pd.notna(r["installed"]) else None,
            importance=float(imp) if pd.notna(imp) else float(cfg["priority"]["default_importance"]))
    return out


def build_context(tables: dict[str, pd.DataFrame], cfg: dict, source: Path) -> DataContext:
    ops = tables["ops"].drop(columns=GROUND_TRUTH_COLUMNS["ops"]).copy()
    ops["time"] = pd.to_datetime(ops["time"])
    ops = ops.sort_values(["pump", "time"]).reset_index(drop=True)
    alarms = tables["alarms"].drop(columns=GROUND_TRUTH_COLUMNS["alarms"]).copy()
    alarms["time"] = pd.to_datetime(alarms["time"])
    wos = tables["work_orders"].copy()
    wos["date"] = pd.to_datetime(wos["date"])
    master = tables["master"].copy()
    return DataContext(cfg=cfg, source=source, master=master, ops=ops, alarms=alarms, work_orders=wos,
                       modes=parse_kb(tables["kb"]), pumps=_pump_infos(master, cfg))


@lru_cache(maxsize=4)
def _cached_context(config_path: str, data_override: Optional[str]) -> DataContext:
    cfg = load_config(Path(config_path))
    source = find_data_file(cfg, data_override)
    tables = _load_tables(source, _resolve(cfg["data"]["cache_dir"]))
    return build_context(tables, cfg, source)


def load_context(config_path: Optional[str | Path] = None, data_path: Optional[str | Path] = None) -> DataContext:
    config_path = str(_resolve(config_path) if config_path else ROOT / "config.yaml")
    return _cached_context(config_path, str(data_path) if data_path else None)


def load_ground_truth(config_path: Optional[str | Path] = None, data_path: Optional[str | Path] = None) -> dict[str, pd.DataFrame]:
    """채점용 정답. 진단 로직(agent/)에서 호출하지 않는다."""
    cfg = load_config(config_path)
    source = find_data_file(cfg, data_path)
    t = _load_tables(source, _resolve(cfg["data"]["cache_dir"]))
    ops = t["ops"][["time", "pump", "state", "label"]].copy()
    ops["time"] = pd.to_datetime(ops["time"])
    alarms = t["alarms"][["alarm_id", "time", "pump", "code", "is_false"]].copy()
    alarms["time"] = pd.to_datetime(alarms["time"])
    return {"labels": ops, "alarms": alarms, "events": t["events"].copy()}
