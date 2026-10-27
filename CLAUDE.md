# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트

펌프 운전 데이터(압력·유량·전류·진동)와 알람·정비 이력으로 이상을 탐지하고, 원인 후보·추가 확인 항목·점검/교체 우선순위를 내는 진단 에이전트입니다. 요구사항은 `PRD.md`, 단계별 개발 지시는 `단계별_프롬프트.md`, 최근 검토 결과와 남은 버그는 `REVIEW.md`에 있습니다. 문서·UI·보고서는 모두 한국어입니다.

## 명령어 (Windows, 가상환경 `.venv`)

```
.venv\Scripts\python -m pytest -q                                   # 전체 테스트
.venv\Scripts\python -m pytest -q tests/test_agent.py::test_p105_example   # 단일 테스트
.venv\Scripts\python scripts/evaluate.py [--quick]                  # PRD 3.3 KPI 평가 → output/kpi_report.md
.venv\Scripts\python cli.py --pump P-105 --latest --no-llm          # 단건 진단 (보고서 output/에 저장)
.venv\Scripts\python cli.py --pump P-105 --discharge 4.47 --flow 107.3 --current 34.0 --vibration 5.61 [--suction 0.78 --bearing-temp 71.7 --signs "씰 누수"]
.venv\Scripts\python cli.py --batch --no-llm                        # 12대 일괄 진단·우선순위표
.venv\Scripts\streamlit run app.py                                  # 화면 (http://localhost:8501)
.venv\Scripts\python gen_dummy_data.py 더미데이터_원본.xlsx <출력.xlsx>   # 확장 더미데이터 재생성 (시드 고정)
```

- 콘솔 한글 출력 시 `python -X utf8` 를 쓰거나 `sys.stdout.reconfigure(encoding="utf-8")` (cp949 콘솔에서 `UnicodeEncodeError`가 남).
- `.streamlit/config.toml` 변경은 Streamlit 서버를 재시작해야 반영됩니다. 파이썬 파일 변경도 실행 중 서버에 바로 반영되지 않는 경우가 있어, 화면 확인 전 재시작하는 것이 안전합니다.
- 린터·포매터 설정은 없습니다.

## 아키텍처

진단 흐름은 `agent/pipeline.py:run_diagnosis` 한 곳에서 이어집니다.

```
loader(엑셀→parquet 캐시, DataContext) → detection(등급·z·추세·알람·오경보) → diagnosis(원인 Top-3)
  → verification(추가 확인 항목) → priority(점수·등급) → report(의견 + .md/.docx/.xlsx)
```

- **판정 기준은 모두 `config.yaml`** 에 있습니다(등급 경계, z-score 데드존, 패턴 수준, 가중치, 우선순위 등급, LLM 모델). 숫자를 코드에 새로 박지 말고 config에 추가하세요.
- **`agent/loader.py`**: 시트·컬럼을 영어 키로 바꿉니다(`SHEETS`). `config.data.paths` 중 필요한 시트를 모두 가진 첫 파일을 사용하므로, 원본 5대 파일(`더미데이터.xlsx`가 아직 원본이면)은 자동으로 건너뛰고 `더미데이터_확장.xlsx`를 씁니다. 엑셀 수정 시각이 바뀌면 `output/cache/` parquet를 다시 만듭니다. `load_context()`는 프로세스 안에서 `lru_cache`로 공유됩니다.
- **`agent/baseline.py`**: 라벨 없이 "정상 범위로 운전한 행"만으로 펌프별 기대값(펌프 중앙값 + 시간대 효과 + 날짜별 전 펌프 공통 효과)과 MAD 표준편차를 추정합니다. 유량·토출/흡입압력·전류는 **정격 대비 비율 공간**에서 다룹니다(`RATIO_VARS`).
- **`agent/detection.py`**: `status_frame`은 벡터 연산이라 단건 진단과 5만 행 전수 평가가 같은 코드를 씁니다. 상태는 이상지수 `D = sqrt(Σ max(0,|z|-deadzone)²)`와 항목 등급으로 정합니다.
- **`agent/diagnosis.py`**: `고장모드_지식베이스` 시트의 화살표(↑↑…↓↓, 압력 열의 "(흡입↓↓)"는 흡입압력으로 분리)를 기대 방향으로 파싱해, 크기 일치도와 z-score 형태 일치도를 섞어 점수를 냅니다. 현장 징후·알람·정비이력 가중을 더하고, 신뢰도는 후보 간 softmax × 적합도 × 미입력 보정입니다.
- **`agent/report.py`**: 수치는 엔진이 계산하고 Claude는 "종합 의견" 문단만 씁니다(`claude-opus-5-5`, `fallbacks="default"`). 응답은 `unsupported_numbers`(식별자·날짜 토큰, 부호·반올림 비교)와 `semantic_problems`(상태·1순위 원인·등급 일치)로 검증하고, 실패하거나 `stop_reason != "end_turn"`이거나 API 키가 없으면 템플릿 문장을 씁니다.
- **UI (`app.py`, `ui/`)**: `st.navigation`에 함수형 페이지 3개(진단/추세/우선순위). 위젯 값은 세션 상태로 관리합니다(`ui/common.py:init_state`). 사이드바 펌프 위젯은 페이지 본문보다 먼저 그려지므로, 다른 페이지에서 펌프를 바꿀 때는 `pending_pump`를 세팅하고 `app.py`에서 처리합니다. 색은 상태 의미 전용(`STATUS`)이며 항상 기호·텍스트와 함께 씁니다.

## 지켜야 할 불변 조건

- **정답 누출 금지**: `상태라벨(정답)`·`오경보(정답)` 열과 `이상이벤트(정답)` 시트는 `build_context`에서 제거되며, `load_ground_truth()`는 `scripts/evaluate.py` 채점에서만 씁니다. `agent/`·`ui/`에서 읽지 마세요.
- **시간 경계**: 진단은 측정 시각 이전의 운전데이터·알람·정비이력만 씁니다(`< time`). `classify_alarm(..., before=time)`도 같은 원칙이며, `before=None`은 사후 평가용입니다. (예외: 기준선은 아직 1년 전체로 학습 — `REVIEW.md` B13)
- **결정적 계산**: 같은 입력이면 같은 결과가 나와야 합니다(`test_deterministic`).
- 측정일시 기본값은 데이터 마지막 시각 + 2시간이고, 데이터 시작 이전이나 마지막 + `validation.max_future_days` 이후는 거부합니다.

## 데이터

- 확장 더미데이터: 펌프 12대 × 2025-10-01~2026-09-30 × 2시간 간격(52,560행), 알람 543건, 정비 90건, 고장 에피소드 40건(조치완료 37, 미조치 3: P-105 베어링, P-109 캐비테이션, P-112 축정렬).
- 이 데이터는 지식베이스 패턴에 맞춰 생성한 가상 데이터라 KPI가 실제보다 낙관적입니다. 판정 기준을 조정할 때는 `scripts/evaluate.py`의 보조 지표(초기 단계, 선택 입력 누락)도 함께 확인하세요.
- `설비마스터`의 집계 열(알람건수 등)은 엑셀 수식이라 pandas로 읽으면 비어 있습니다.
- 엑셀이 열려 있으면 `더미데이터.xlsx`를 덮어쓸 수 없습니다(`~$더미데이터.xlsx` 잠금 파일).

## 테스트 관련 주의

- `tests/test_app.py`는 Streamlit `AppTest`를 씁니다. `AppTest.from_file` 경로는 테스트 파일 기준(`"../app.py"`)이고, 함수형 페이지는 `switch_page`로 갈 수 없어 `from_string`으로 페이지 함수를 직접 실행합니다.
- `st.number_input`은 `value=None`을 명시해야 사용자가 칸을 비웠을 때 0이 아닌 None이 됩니다(선택 입력 처리에 필수).
- LLM 경로는 가짜 클라이언트로만 테스트합니다(`test_llm_opinion_guarded`). 실제 호출은 `ANTHROPIC_API_KEY`가 있을 때만 일어납니다.
