"""펌프 이상진단 더미데이터 확장 생성기 (12대 x 1년 x 2시간 간격)
usage: python gen_data.py <원본.xlsx> <출력.xlsx>
"""
import sys, math, random
from copy import copy
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

SRC, DST = sys.argv[1], sys.argv[2]
SEED = 20261027
rng = np.random.default_rng(SEED)
random.seed(SEED)

START = datetime(2025, 10, 1)
END = datetime(2026, 9, 30, 22)
times = pd.date_range(START, END, freq="2h")
N = len(times)
DAY = 12  # rows per day

# id, 설비명, 위치, 형식, kW, 정격유량, 정격토출, 정격흡입, 정격전류, 설치일, 설비중요도(점수)
PUMPS = [
    ("P-101", "냉각수 순환펌프 #1", "A동 기계실", "원심(단단)", 18.5, 120, 4.5, 0.8, 38, datetime(2019, 3, 15), 70),
    ("P-102", "공정수 이송펌프 #2", "B동 공정라인", "원심(단단)", 15, 80, 6.0, 1.0, 29, datetime(2020, 6, 20), 60),
    ("P-103", "원수 취수펌프 #3", "취수장", "원심(다단)", 37, 200, 5.5, 0.5, 62, datetime(2017, 11, 2), 90),
    ("P-104", "소방 보조펌프 #4", "C동 지하", "원심(단단)", 11, 60, 7.0, 1.2, 24, datetime(2021, 1, 10), 100),
    ("P-105", "냉각수 순환펌프 #5", "A동 기계실", "원심(단단)", 18.5, 120, 4.5, 0.8, 38, datetime(2022, 8, 5), 70),
    ("P-106", "냉각수 순환펌프 #6", "A동 기계실", "원심(단단)", 18.5, 120, 4.5, 0.8, 38, datetime(2023, 4, 12), 70),
    ("P-107", "보일러 급수펌프 #1", "보일러실", "원심(다단)", 45, 60, 12.0, 1.5, 78, datetime(2018, 9, 3), 90),
    ("P-108", "보일러 급수펌프 #2", "보일러실", "원심(다단)", 45, 60, 12.0, 1.5, 78, datetime(2018, 9, 3), 90),
    ("P-109", "슬래그 냉각수펌프", "제강공장", "원심(단단)", 55, 300, 4.0, 0.6, 96, datetime(2016, 5, 20), 85),
    ("P-110", "공정수 이송펌프 #3", "B동 공정라인", "원심(단단)", 15, 80, 6.0, 1.0, 29, datetime(2021, 11, 11), 60),
    ("P-111", "폐수 이송펌프", "폐수처리장", "원심(단단)", 11, 50, 3.5, 0.6, 22, datetime(2019, 7, 1), 40),
    ("P-112", "소방 주펌프", "C동 지하", "원심(다단)", 30, 120, 8.0, 1.2, 55, datetime(2021, 1, 10), 100),
]
PI = {p[0]: p for p in PUMPS}

BEAR, SEAL, CAV, MIS, IMP, MOT, PIPE = (
    "베어링 열화", "메카니컬씰 누설", "캐비테이션/임펠러 마모", "축정렬 불량",
    "임펠러 불평형/이물", "모터 이상(권선/전원)", "배관 막힘/밸브 이상")

ROOTS = {
    BEAR: ["윤활 불량", "피로 수명 도달(내륜 박리)", "이물질 혼입"],
    SEAL: ["씰 접촉면 마모·O링 경화", "플러싱 라인 막힘", "건조운전"],
    CAV: ["흡입 스트레이너 이물 막힘", "흡입측 밸브 부분 개도", "흡수정 수위 저하"],
    MIS: ["축정렬 불량(편심)", "베이스 볼트 이완", "소프트풋"],
    IMP: ["임펠러 이물 부착", "임펠러 불평형(침식)"],
    MOT: ["권선 절연 열화", "3상 전원 불평형", "모터 냉각팬 손상"],
    PIPE: ["토출 체크밸브 고착", "배관 스케일 퇴적", "토출밸브 개도 불량"],
}
DUR = {BEAR: (22, 40), SEAL: (14, 28), CAV: (12, 24), MIS: (10, 22), IMP: (10, 20), MOT: (8, 18), PIPE: (6, 14)}
FIELD_SIGN = {BEAR: "이상음(금속음)", SEAL: "씰부 누수", CAV: "자갈 구르는 소리", MIS: "커플링 발열",
              IMP: None, MOT: "모터 발열", PIPE: None}
# 오진단(먼저 엉뚱한 부위를 점검한 경우) 대상
WRONG_PART = {BEAR: "커플링/축정렬", SEAL: "토출밸브", CAV: "베어링", MIS: "베어링", IMP: "모터", MOT: "베어링", PIPE: "임펠러"}


def episode(pump, mode, end, dur, p=None, f=None, root=None, ongoing=False):
    """end: 정비 착수일(ongoing이면 데이터 끝). dur: s=1에 도달하는 일수, f: 정비 시점 진행률"""
    p = p or round(float(rng.uniform(1.3, 2.1)), 2)
    f = f or round(float(rng.uniform(0.8, 1.0)), 2)
    if ongoing:
        start = END - timedelta(days=dur * f)
        end_t = END + timedelta(hours=2)
    else:
        end_t = datetime(end.year, end.month, end.day, 8)
        start = end_t - timedelta(days=dur * f)
    return dict(pump=pump, mode=mode, start=start, end=end_t, D=dur, p=p, f=f,
                root=root or random.choice(ROOTS[mode]), ongoing=ongoing)


EPIS = [
    # 원본 시나리오와 이어지는 고정 에피소드
    episode("P-101", SEAL, datetime(2025, 11, 12), 20, f=0.75, root="씰면 마모"),
    episode("P-101", BEAR, datetime(2026, 8, 18), 34, f=1.0, root="피로 수명 도달(내륜 박리)"),
    episode("P-102", IMP, datetime(2026, 2, 20), 16),
    episode("P-102", SEAL, datetime(2026, 9, 4), 24, f=0.95, root="씰 접촉면 마모·O링 경화"),
    episode("P-103", CAV, datetime(2026, 1, 25), 18, f=0.65, root="흡입 스트레이너 이물 막힘"),
    episode("P-103", CAV, datetime(2026, 8, 7), 20, f=1.0, root="흡입 스트레이너 이물 막힘"),
    episode("P-104", BEAR, datetime(2026, 3, 17), 28, f=0.95, root="이물질 혼입"),
    episode("P-104", MIS, datetime(2026, 9, 10), 18, f=1.0, root="축정렬 불량(편심)"),
    episode("P-105", PIPE, datetime(2026, 4, 10), 10),
    # 미조치(진단 대상)
    episode("P-105", BEAR, None, 34, p=1.4, f=0.72, root="윤활 불량", ongoing=True),
    episode("P-109", CAV, None, 20, p=1.3, f=0.7, root="흡수정 수위 저하", ongoing=True),
    episode("P-112", MIS, None, 18, p=1.5, f=0.55, root="베이스 볼트 이완", ongoing=True),
]

# 나머지 펌프 랜덤 에피소드 (고장모드 균형)
pool = [MOT] * 5 + [PIPE] * 4 + [IMP] * 4 + [MIS] * 4 + [CAV] * 3 + [SEAL] * 4 + [BEAR] * 4
random.shuffle(pool)
slots = [(datetime(2025, 10, 20), datetime(2025, 12, 31)), (datetime(2026, 1, 10), datetime(2026, 3, 31)),
         (datetime(2026, 4, 15), datetime(2026, 6, 20)), (datetime(2026, 7, 1), datetime(2026, 9, 5))]
for pid in ["P-106", "P-107", "P-108", "P-109", "P-110", "P-111", "P-112"]:
    for (a, b) in slots:
        mode = pool.pop()
        dur = int(rng.integers(*DUR[mode]))
        lo = a + timedelta(days=dur)
        end = lo + timedelta(days=int(rng.integers(0, max(1, (b - lo).days))))
        if pid in ("P-109", "P-112") and b.month == 9:
            end = min(end, datetime(2026, 8, 20))
        EPIS.append(episode(pid, mode, end, dur))

ALARM_ROWS = []   # (pid, i, code, name, grade, value, thr, note, false)
WOS = []
EVENTS = []

ops_frames = []
false_spikes = {}  # (pid, i) -> note

doy = times.dayofyear.values
hour = times.hour.values
amb = 4.0 * np.sin(2 * np.pi * (doy - 130) / 365)

for pid, name, loc, typ, kw, RF, RD, RS, RC, inst, imp in PUMPS:
    fr = rng.uniform(0.90, 0.95); dr = rng.uniform(0.98, 1.0)
    cr = rng.uniform(0.88, 0.93); vb = rng.uniform(1.4, 2.0); tb = rng.uniform(51, 55)
    # 부하 변동: 일주기 + AR(1)
    ar = np.zeros(N); e = rng.normal(0, 0.008, N)
    for i in range(1, N):
        ar[i] = 0.85 * ar[i - 1] + e[i]
    L = 1 + 0.025 * np.sin(2 * np.pi * (hour - 8) / 24) + ar
    flow = RF * fr * L * (1 + rng.normal(0, 0.006, N))
    dis = RD * dr * (1 - 0.3 * (L - 1)) + rng.normal(0, 0.008 * RD, N)
    cur = RC * cr * (1 + 0.6 * (L - 1)) * (1 + rng.normal(0, 0.008, N))
    suc = RS * (1 + 0.02 * np.sin(2 * np.pi * doy / 365) + rng.normal(0, 0.025, N))
    vib = vb + 0.6 * (L - 1) * 10 * 0.1 + rng.normal(0, 0.12, N)
    temp = tb + amb + 15 * (L - 1) + rng.normal(0, 0.6, N)
    sev = np.zeros(N)
    down = np.zeros(N, bool)
    down_note = {}

    def apply(mode, s, idx):
        n = len(idx)
        if mode == BEAR:
            vib[idx] += rng.uniform(5.8, 6.8) * s; temp[idx] += rng.uniform(21, 27) * s; cur[idx] *= 1 + 0.04 * s
        elif mode == SEAL:
            vib[idx] += 1.4 * s; cur[idx] *= 1 - 0.04 * s; dis[idx] *= 1 - 0.14 * s; flow[idx] *= 1 - 0.16 * s
        elif mode == CAV:
            suc[idx] -= RS * 0.6 * s; flow[idx] *= 1 - 0.15 * s; dis[idx] *= 1 - 0.13 * s
            vib[idx] += 3.4 * s + np.abs(rng.normal(0, 1.0, n)) * s
            cur[idx] *= 1 + rng.normal(0, 0.05, n) * s
        elif mode == MIS:
            vib[idx] += 4.6 * s; cur[idx] *= 1 + 0.25 * s; temp[idx] += 12 * s
        elif mode == IMP:
            vib[idx] += 3.3 * s; cur[idx] *= 1 + 0.04 * s; flow[idx] *= 1 - 0.07 * s; dis[idx] *= 1 - 0.03 * s
        elif mode == MOT:
            cur[idx] *= 1 + 0.28 * s + rng.normal(0, 0.02, n) * s; vib[idx] += 1.0 * s; temp[idx] += 4 * s
        elif mode == PIPE:
            flow[idx] *= 1 - 0.22 * s; dis[idx] *= 1 + 0.14 * s; cur[idx] *= 1 - 0.08 * s; vib[idx] += 0.3 * s

    def mark_down(t0, hours, note):
        i0 = times.searchsorted(t0)
        k = max(1, math.ceil(hours / 2))
        idx = np.arange(i0, min(N, i0 + k))
        down[idx] = True
        for i in idx:
            down_note[i] = note
        return idx

    for ep in [e for e in EPIS if e["pump"] == pid]:
        i0, i1 = times.searchsorted(ep["start"]), min(N, times.searchsorted(ep["end"]))
        idx = np.arange(i0, i1)
        t = (times[idx] - ep["start"]).total_seconds().values / 86400
        s = np.clip(t / ep["D"], 0, 1) ** ep["p"]
        apply(ep["mode"], s, idx)
        sev[idx] = np.maximum(sev[idx], s)
        ep["idx"] = idx
        ep["s_end"] = float(s[-1])

    # 파생값 정리 후 정지 구간 반영
    vib = np.abs(vib)
    # 정비 이력 생성
    pre_wo = []
    for ep in [e for e in EPIS if e["pump"] == pid]:
        idx = ep["idx"]
        ev = dict(이벤트ID=None, 펌프ID=pid, 고장모드=ep["mode"], 근본원인=ep["root"], 시작일시=times[idx[0]].to_pydatetime())
        sx = sev[idx]
        ev["이상징후 시작"] = times[idx[np.argmax(sx >= 0.15)]].to_pydatetime() if (sx >= 0.15).any() else None
        ev["이상 시작"] = times[idx[np.argmax(sx >= 0.5)]].to_pydatetime() if (sx >= 0.5).any() else None
        ev["최대 진행률"] = round(ep["s_end"], 2)
        # 오진단 점검 (진행률 0.5 무렵, 일부 에피소드)
        if not ep["ongoing"] and random.random() < 0.25 and (sx >= 0.5).any():
            j = idx[np.argmax(sx >= 0.5)]
            t_wrong = times[j].to_pydatetime() + timedelta(days=1)
            t_wrong = datetime(t_wrong.year, t_wrong.month, t_wrong.day, 8)
            part = WRONG_PART[ep["mode"]]
            pre_wo.append(dict(작업일자=t_wrong, 펌프ID=pid, 작업유형="점검", 대상부품=part, **{"고장원인(확인)": "-"},
                               **{"작업 전 증상": None, "_i": j}, 조치내용=f"{part} 점검, 이상 없음",
                               **{"작업시간(h)": 2, "비용(원)": 200000, "비고": "원인 미확인, 증상 지속(오진단)"}))
            mark_down(t_wrong, 2, "점검")
        if ep["ongoing"]:
            ev.update({"정비일자": None, "작업ID": None, "상태": "미조치(진행 중)"})
        else:
            ev.update({"정비일자": ep["end"], "작업ID": None, "상태": "조치완료"})
        ep["ev"] = ev
        EVENTS.append(ev)

    # 시운전 직후 진동 스파이크 / 순간 오경보 스파이크
    # 정지 구간 먼저 계산
    for ep in [e for e in EPIS if e["pump"] == pid and not e["ongoing"]]:
        m = ep["mode"]
        if m == BEAR:
            typ_, part, act, h, cost = "교체", "베어링(구동측/반구동측)", "베어링 2개 교체, 그리스 재충진", rng.uniform(5, 7), rng.uniform(1.6e6, 2.0e6)
        elif m == SEAL:
            typ_, part, act, h, cost = "교체", "메카니컬씰", "메카니컬씰 교체, 플러싱 라인 청소", rng.uniform(7, 9), rng.uniform(2.2e6, 2.6e6)
        elif m == CAV and ep["s_end"] < 0.6:
            typ_, part, act, h, cost = "수리", "흡입 스트레이너", "흡입 스트레이너 청소, 흡입밸브 개도 확인", 3, rng.uniform(2.5e5, 3.5e5)
        elif m == CAV:
            typ_, part, act, h, cost = "교체", "임펠러", "임펠러 교체(캐비테이션 침식), 흡입 스트레이너 청소", rng.uniform(10, 13), rng.uniform(3.8e6, 4.5e6)
        elif m == MIS:
            typ_, part, act, h, cost = "수리", "커플링/축정렬", "레이저 얼라인먼트 재정렬, 커플링 고무 교체, 베이스 볼트 재체결", rng.uniform(4, 6), rng.uniform(8e5, 1.0e6)
        elif m == IMP:
            typ_, part, act, h, cost = "수리", "임펠러", "임펠러 이물 제거, 동적 밸런싱", rng.uniform(6, 8), rng.uniform(1.0e6, 1.5e6)
        elif m == MOT and ep["root"] == "권선 절연 열화":
            typ_, part, act, h, cost = "교체", "모터", "모터 재권선(외주), 절연저항 측정", rng.uniform(16, 24), rng.uniform(3.5e6, 5.0e6)
        elif m == MOT and ep["root"] == "3상 전원 불평형":
            typ_, part, act, h, cost = "수리", "전원/MCC", "MCC 단자 재체결, 3상 전원 불평형 조정", 3, rng.uniform(3e5, 5e5)
        elif m == MOT:
            typ_, part, act, h, cost = "교체", "모터 냉각팬", "모터 냉각팬 교체", 3, rng.uniform(4e5, 6e5)
        else:
            typ_, part, act, h, cost = "수리", "토출 체크밸브/배관", "체크밸브 정비, 배관 스케일 제거, 밸브 개도 조정", rng.uniform(5, 8), rng.uniform(9e5, 1.6e6)
        h = round(float(h)); cost = round(float(cost), -4)
        ep["wo"] = dict(작업일자=ep["end"], 펌프ID=pid, 작업유형=typ_, 대상부품=part, **{"고장원인(확인)": ep["root"]},
                        **{"작업 전 증상": None, "_i": ep["idx"][-1]}, 조치내용=act,
                        **{"작업시간(h)": h, "비용(원)": int(cost), "비고": None})
        di = mark_down(ep["end"], h, "정비")
        ep["after"] = di[-1] + 1 if len(di) else None

    # 정기점검 (분기 1회)
    for q in [(2025, 10), (2026, 1), (2026, 4), (2026, 7)]:
        d = datetime(q[0], q[1], int(rng.integers(3, 26)), 8)
        i = times.searchsorted(d)
        near = [e for e in EPIS if e["pump"] == pid and e["start"] - timedelta(days=3) <= d <= e["end"] + timedelta(days=7)]
        if near or i >= N:
            continue
        pre_wo.append(dict(작업일자=d, 펌프ID=pid, 작업유형="점검", 대상부품="정기점검", **{"고장원인(확인)": "-"},
                           **{"작업 전 증상": "이상 없음", "_i": None}, 조치내용=random.choice(
                               ["진동 측정, 그리스 보충", "윤활유 보충, 볼트 체결 확인", "진동·온도 측정, 씰부 누수 점검", "그리스 보충, 커플링 육안 점검"]),
                           **{"작업시간(h)": 2, "비용(원)": 150000, "비고": "예방정비"}))
        di = mark_down(d, 2, "정기점검")
        if random.random() < 0.3 and di[-1] + 1 < N:
            false_spikes[(pid, di[-1] + 1)] = ("vib", "정기점검 후 재기동 중 일시 발생, 오경보")

    for ep in [e for e in EPIS if e["pump"] == pid and not e["ongoing"]]:
        if ep.get("after") is not None and ep["after"] < N and random.random() < 0.4:
            false_spikes[(pid, ep["after"])] = ("vib", "정비 후 시운전 중 일시 발생, 오경보")
    # 순간 부하 변동 / 센서 노이즈 오경보 (정상 구간)
    normal_idx = np.where((sev < 0.05) & ~down)[0]
    for i in rng.choice(normal_idx, size=int(rng.integers(3, 6)), replace=False):
        kind = random.choice(["cur", "vib", "vib"])
        false_spikes[(pid, int(i))] = (kind, "순간 부하 변동, 오경보" if kind == "cur" else random.choice(["센서 노이즈, 오경보", "센서 케이블 접촉 불량, 오경보"]))

    for (p_, i), (kind, note) in false_spikes.items():
        if p_ != pid or down[i]:
            continue
        if kind == "vib":
            vib[i] = rng.uniform(4.6, 5.4)
        else:
            cur[i] = RC * rng.uniform(1.06, 1.10)

    # 정지 구간 값
    flow[down] = 0; cur[down] = 0; vib[down] = 0; dis[down] = suc[down]
    temp[down] = tb + amb[down] - 3

    label = np.where(down, "정비", np.where(sev >= 0.5, "이상", np.where(sev >= 0.15, "이상징후", "정상")))
    state = np.array(["운전"] * N, dtype=object)
    for i, note in down_note.items():
        state[i] = "정지(정비)" if note == "정비" else "정지(점검)"

    df = pd.DataFrame({
        "일시": times, "펌프ID": pid, "흡입압력(bar)": np.round(suc, 2), "토출압력(bar)": np.round(dis, 2),
        "유량(m³/h)": np.round(flow, 1), "전류(A)": np.round(cur, 1), "진동RMS(mm/s)": np.round(vib, 2),
        "베어링온도(℃)": np.round(temp, 1), "운전상태": state, "상태라벨(정답)": label})
    ops_frames.append(df)

    # 작업 전 증상 텍스트
    def symptom(i, mode):
        w = slice(max(0, i - 2), i + 1)
        v, tp, c, f, d, sc = (df[k].values[w].mean() for k in ["진동RMS(mm/s)", "베어링온도(℃)", "전류(A)", "유량(m³/h)", "토출압력(bar)", "흡입압력(bar)"])
        parts = []
        if v > 2.8: parts.append(f"진동 {v:.1f} mm/s")
        if tp > 65: parts.append(f"베어링온도 {tp:.0f}℃")
        if c > RC * 1.03: parts.append(f"전류 {c:.1f} A(정격 대비 +{(c / RC - 1) * 100:.0f}%)")
        if c < RC * 0.85: parts.append(f"전류 {c:.1f} A 저하")
        if f < RF * 0.88: parts.append(f"유량 {f:.0f} m³/h")
        if d < RD * 0.92: parts.append(f"토출압력 {d:.2f} bar")
        if d > RD * 1.06: parts.append(f"토출압력 {d:.2f} bar 상승")
        if sc < RS * 0.7: parts.append(f"흡입압력 {sc:.2f} bar")
        if FIELD_SIGN[mode] and sev[i] > 0.4: parts.append(FIELD_SIGN[mode])
        return ", ".join(parts) or "경미한 변화"

    for ep in [e for e in EPIS if e["pump"] == pid and not e["ongoing"]]:
        wo = ep["wo"]; wo["작업 전 증상"] = symptom(wo.pop("_i"), ep["mode"])
        if ep["mode"] == CAV and "스트레이너" in ep["root"]:
            wo["비고"] = "흡입 스트레이너 막힘이 근본원인"
        else:
            wo["비고"] = random.choice(["정상 복귀", "진동·전류 정상 복귀", "누수 해소, 압력·유량 회복" if ep["mode"] == SEAL else "운전값 정상 복귀"])
        wo["_ev"] = ep["ev"]
        WOS.append(wo)
    for wo in pre_wo:
        i = wo.pop("_i")
        if i is not None:
            m = next(e["mode"] for e in EPIS if e["pump"] == pid and e["idx"][0] <= i <= e["idx"][-1])
            wo["작업 전 증상"] = symptom(i, m)
        WOS.append(wo)

    # ---- 알람 생성 (임계값 초과, 같은 코드 24시간 내 중복 억제) ----
    run = ~down
    conds = [
        ("VIB-H1", "진동 주의", "주의", vib, vib > 4.5, 4.5),
        ("VIB-H2", "진동 경고", "경고", vib, vib > 7.1, 7.1),
        ("TMP-H1", "베어링온도 높음", "주의", temp, temp > 70, 70),
        ("TMP-H2", "베어링온도 경고", "경고", temp, temp > 80, 80),
        ("CUR-H1", "과전류 주의", "주의", cur, cur > RC * 1.05, round(RC * 1.05, 1)),
        ("CUR-H2", "과전류 경고", "경고", cur, cur > RC * 1.15, round(RC * 1.15, 1)),
        ("FLW-L1", "유량 저하", "주의", flow, flow < RF * 0.85, round(RF * 0.85, 1)),
        ("PRS-L1", "토출압력 저하", "주의", dis, dis < RD * 0.88, round(RD * 0.88, 2)),
        ("PRS-H1", "토출압력 상승", "주의", dis, dis > RD * 1.10, round(RD * 1.10, 2)),
        ("SUC-L1", "흡입압력 저하(캐비테이션 위험)", "경고", suc, suc < RS * 0.6, round(RS * 0.6, 2)),
    ]
    for code, nm, grade, arr, cond, thr in conds:
        last = None
        for i in np.where(cond & run)[0]:
            if last is not None and (times[i] - times[last]).total_seconds() < 86400:
                continue
            last = i
            fs = false_spikes.get((pid, int(i)))
            note = fs[1] if fs else None
            ALARM_ROWS.append((pid, int(i), code, nm, grade, round(float(arr[i]), 2), thr, note, bool(fs), False))
    # 현장/순찰 알람
    for ep in [e for e in EPIS if e["pump"] == pid]:
        idx, m = ep["idx"], ep["mode"]
        cand = idx[sev[idx] > 0.45]
        if len(cand) == 0:
            continue
        picks = sorted(rng.choice(cand, size=min(len(cand), int(rng.integers(1, 3))), replace=False))
        for i in picks:
            i = int(i)
            if m == BEAR:
                ALARM_ROWS.append((pid, i, "NOISE", "베어링부 이상음", "주의", None, None, "운전원 보고", False, True))
            elif m == CAV:
                ALARM_ROWS.append((pid, i, "NOISE", "이상소음(자갈 굴러가는 소리)", "주의", None, None, "운전원 보고", False, True))
            elif m == SEAL:
                ALARM_ROWS.append((pid, i, "SEAL-LK", "씰 누설 감지(누설센서)", "경고", None, None, random.choice(["순찰 중 씰부 누수 확인", "누수량 증가, 바닥 고임"]), False, True))
            elif m == MIS:
                ALARM_ROWS.append((pid, i, "COUP-T", "커플링 온도 상승(열화상 점검)", "주의", None, None, f"커플링 표면 {int(rng.integers(62, 76))}℃", False, True))
            elif m == MOT:
                ALARM_ROWS.append((pid, i, "MOT-T", "모터 권선온도 상승", "주의", round(float(rng.uniform(112, 128)), 1), 110, "모터 프레임 발열 확인", False, True))

# ---------------- 정리 ----------------
ops = pd.concat(ops_frames).sort_values(["일시", "펌프ID"], kind="stable").reset_index(drop=True)

alarms = []
for pid, i, code, nm, grade, val, thr, note, fal, field in ALARM_ROWS:
    t = times[i].to_pydatetime() + timedelta(minutes=int(rng.integers(0, 115)))
    clr = t + timedelta(minutes=int(rng.integers(30, 600)))
    alarms.append({"발생일시": t, "펌프ID": pid, "알람코드": code, "알람명": nm, "등급": grade, "측정값": val, "임계값": thr,
                   "해제일시": clr if clr <= END else None, "비고": note, "오경보(정답)": "Y" if fal else "N"})
alarms = pd.DataFrame(alarms).sort_values("발생일시").reset_index(drop=True)
alarms.insert(0, "알람ID", [f"AL-{k + 1:05d}" for k in range(len(alarms))])

# 기간 이전 과거 이력 (원본 유지)
WOS += [
    dict(작업일자=datetime(2025, 5, 21), 펌프ID="P-103", 작업유형="교체", 대상부품="베어링", **{"고장원인(확인)": "윤활 불량"},
         **{"작업 전 증상": "진동 7.5 mm/s 이상, 온도 82℃"}, 조치내용="베어링 교체, 윤활주기 단축", **{"작업시간(h)": 6, "비용(원)": 1900000, "비고": "과거 이력"}),
    dict(작업일자=datetime(2025, 9, 3), 펌프ID="P-102", 작업유형="수리", 대상부품="커플링/축정렬", **{"고장원인(확인)": "축정렬 불량"},
         **{"작업 전 증상": "전류 +15%, 진동 5.2 mm/s"}, 조치내용="축정렬 재조정", **{"작업시간(h)": 4, "비용(원)": 800000, "비고": "과거 이력"}),
]
WOS.sort(key=lambda w: (w["작업일자"], w["펌프ID"]))
for k, w in enumerate(WOS):
    w["작업ID"] = f"WO-{k + 1:04d}"
    if "_ev" in w:
        w.pop("_ev")["작업ID"] = w["작업ID"]
wos = pd.DataFrame(WOS)[["작업ID", "작업일자", "펌프ID", "작업유형", "대상부품", "고장원인(확인)", "작업 전 증상", "조치내용", "작업시간(h)", "비용(원)", "비고"]]

EVENTS.sort(key=lambda e: (e["시작일시"], e["펌프ID"]))
for k, e in enumerate(EVENTS):
    e["이벤트ID"] = f"EV-{k + 1:03d}"
events = pd.DataFrame(EVENTS)[["이벤트ID", "펌프ID", "고장모드", "근본원인", "시작일시", "이상징후 시작", "이상 시작", "정비일자", "작업ID", "최대 진행률", "상태"]]

# ---------------- 엑셀 쓰기 ----------------
src = openpyxl.load_workbook(SRC)
HDR = src["설비마스터"]["A1"]
thin = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
DFONT = Font(size=10)

wb = openpyxl.Workbook()
wb.remove(wb.active)


def style_header(ws, ncol):
    for c in range(1, ncol + 1):
        cell = ws.cell(1, c)
        cell.font = copy(HDR.font); cell.fill = copy(HDR.fill); cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = copy(HDR.border)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"


def write_df(ws, df, widths, fmts=None, light=False):
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append([None if (isinstance(v, float) and math.isnan(v)) or v is pd.NaT else
                   (v.to_pydatetime() if isinstance(v, pd.Timestamp) else v) for v in row])
    style_header(ws, df.shape[1])
    for k, w in enumerate(widths):
        ws.column_dimensions[openpyxl.utils.get_column_letter(k + 1)].width = w
    fmts = fmts or {}
    if not light:
        for r in ws.iter_rows(min_row=2):
            for c in r:
                c.font = DFONT; c.border = BORDER
    for col, f in fmts.items():
        for (c,) in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            c.number_format = f
    ws.auto_filter.ref = ws.dimensions


# README
ws = wb.create_sheet("README")
n_ab = int((ops["상태라벨(정답)"] != "정상").sum())
readme = [
    ("펌프 이상진단 에이전트 – 더미데이터 (확장판)", None), (None, None), ("시트", "내용"),
    ("설비마스터", f"펌프 {len(PUMPS)}대 기본정보·정격값·설비중요도, 시트별 건수 요약(수식)"),
    ("운전데이터", f"{START:%Y-%m-%d} ~ {END:%Y-%m-%d}, 2시간 간격 센서값 {len(ops):,}건(압력·유량·전류·진동·베어링온도) + 정답 라벨"),
    ("알람이력", f"임계값 초과 시 자동 생성된 알람(같은 코드 24시간 내 중복 억제) + 현장/순찰 알람, 총 {len(alarms):,}건 (오경보 {int((alarms['오경보(정답)'] == 'Y').sum())}건, '오경보(정답)' 열로 표시)"),
    ("정비이력", f"교체/수리/점검 이력 {len(wos)}건 – 작업 전 증상과 확인된 고장원인. 정기점검, 오진단 점검(원인 미확인) 포함"),
    ("이상이벤트(정답)", f"고장 에피소드 {len(events)}건의 고장모드·근본원인·이상징후/이상 시작 시점·정비 작업ID (검증용 정답)"),
    ("임계값기준", "항목별 정상/관찰/주의/경고 구간 (흡입압력, 토출압력 상승 추가)"),
    ("고장모드_지식베이스", "고장모드별 증상 패턴, 추가 확인 항목, 권장 점검 순서 (원인후보 분석용)"),
    (None, None), ("진단 대상(미조치, 정답)", None),
]
for e in EVENTS:
    if e["상태"].startswith("미조치"):
        readme.append((e["펌프ID"], f"{e['고장모드']} – 진행 중 (근본원인: {e['근본원인']})"))
readme += [
    (None, None),
    ("고장모드별 에피소드 수", ", ".join(f"{m} {c}" for m, c in events["고장모드"].value_counts().items())),
    ("상태라벨 기준", "고장 진행률 0.15 미만 정상 / 0.15~0.5 이상징후 / 0.5 이상 이상 / 정지 구간 정비 (이상·이상징후 합계 " + f"{n_ab:,}건)"),
    ("알람코드", "VIB-H1/H2 진동, TMP-H1/H2 베어링온도, CUR-H1/H2 전류, FLW-L1 유량, PRS-L1/H1 토출압력, SUC-L1 흡입압력, NOISE/SEAL-LK/COUP-T/MOT-T 현장 징후"),
    ("활용 방법", "① 운전데이터로 이상탐지 → ② 패턴을 지식베이스와 비교해 원인후보 산출 → ③ 정비이력·이상이벤트로 학습/검증 → ④ 미조치 펌프를 진단 대상으로 보고서·우선순위 생성"),
    ("원본", "더미데이터_원본.xlsx (5대 × 3개월, 4시간 간격)"),
    ("주의", "모든 값은 가상 데이터입니다. 임계값은 ISO 10816-3 및 일반 경험값을 참고한 가정치입니다. 설비마스터의 집계 열은 수식이므로 pandas로 읽으면 비어 있을 수 있습니다."),
]
for r in readme:
    ws.append(list(r))
ws["A1"].font = Font(bold=True, size=14)
for r in (3,):
    for c in (1, 2):
        ws.cell(r, c).font = copy(HDR.font); ws.cell(r, c).fill = copy(HDR.fill)
for r in range(1, ws.max_row + 1):
    if ws.cell(r, 1).value in ("진단 대상(미조치, 정답)",):
        ws.cell(r, 1).font = Font(bold=True)
ws.column_dimensions["A"].width = 24; ws.column_dimensions["B"].width = 120

# 설비마스터
ws = wb.create_sheet("설비마스터")
cols = ["펌프ID", "설비명", "위치", "형식", "모터(kW)", "정격유량(m³/h)", "정격토출압력(bar)", "정격흡입압력(bar)", "정격전류(A)", "설치일", "설비중요도(점수)",
        "알람건수", "경고알람", "정비건수", "교체건수", "최근 정비일"]
ws.append(cols)
for k, p in enumerate(PUMPS):
    r = k + 2
    ws.append(list(p) + [f"=COUNTIF(알람이력!$C:$C,A{r})", f'=COUNTIFS(알람이력!$C:$C,A{r},알람이력!$F:$F,"경고")',
                         f"=COUNTIF(정비이력!$C:$C,A{r})", f'=COUNTIFS(정비이력!$C:$C,A{r},정비이력!$D:$D,"교체")',
                         f'=IFERROR(1/(1/SUMPRODUCT(MAX((정비이력!$C$2:$C$1000=A{r})*정비이력!$B$2:$B$1000))),"-")'])
    ws.cell(r, 10).number_format = "yyyy-mm-dd"; ws.cell(r, 16).number_format = "yyyy-mm-dd"
style_header(ws, len(cols))
for r in ws.iter_rows(min_row=2):
    for c in r:
        c.font = DFONT; c.border = BORDER
for k, w in enumerate([10, 22, 14, 13, 10, 14, 16, 16, 12, 12, 14, 10, 10, 10, 10, 13]):
    ws.column_dimensions[openpyxl.utils.get_column_letter(k + 1)].width = w
ws.auto_filter.ref = f"A1:K{len(PUMPS) + 1}"

write_df(wb.create_sheet("운전데이터"), ops, [17, 9, 13, 13, 12, 10, 14, 13, 11, 13], {1: "yyyy-mm-dd hh:mm"}, light=True)
write_df(wb.create_sheet("알람이력"), alarms, [12, 17, 9, 10, 28, 7, 10, 10, 17, 32, 12], {2: "yyyy-mm-dd hh:mm", 9: "yyyy-mm-dd hh:mm"})
write_df(wb.create_sheet("정비이력"), wos, [10, 12, 9, 9, 26, 26, 60, 50, 10, 12, 30], {2: "yyyy-mm-dd", 10: "#,##0"})
write_df(wb.create_sheet("이상이벤트(정답)"), events, [10, 9, 22, 24, 17, 17, 17, 17, 10, 11, 16],
         {5: "yyyy-mm-dd hh:mm", 6: "yyyy-mm-dd hh:mm", 7: "yyyy-mm-dd hh:mm", 8: "yyyy-mm-dd"})

# 임계값기준 (원본 + 추가 2행)
th = pd.read_excel(SRC, sheet_name="임계값기준")
th = pd.concat([th, pd.DataFrame([
    {"항목": "흡입압력(정격 대비)", "정상": "≥ 80%", "관찰": "70 ~ 80%", "주의": "60 ~ 70%", "경고": "< 60%", "근거": "NPSH 여유 기준(가정), SUC-L1 = 정격 60% 미만"},
    {"항목": "토출압력 상승(정격 대비)", "정상": "≤ 105%", "관찰": "105 ~ 110%", "주의": "> 110%", "경고": "-", "근거": "배관 막힘/밸브 이상 판단(가정), PRS-H1"},
])], ignore_index=True)
write_df(wb.create_sheet("임계값기준"), th, [24, 12, 14, 12, 12, 44])
kb = pd.read_excel(SRC, sheet_name="고장모드_지식베이스")
write_df(wb.create_sheet("고장모드_지식베이스"), kb, [22, 16, 14, 12, 10, 11, 18, 50, 55])
for ws in (wb["정비이력"], wb["고장모드_지식베이스"]):
    for r in ws.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")

wb.save(DST)
print("rows", len(ops), "alarms", len(alarms), "wos", len(wos), "events", len(events))
