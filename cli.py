"""펌프 이상진단 명령행 도구.

예)
  python cli.py --pump P-105 --discharge 4.47 --flow 107.3 --current 34.0 --vibration 5.61 --suction 0.78 --bearing-temp 71.7
  python cli.py --pump P-105 --latest          # 데이터의 마지막 운전값으로 진단
  python cli.py --batch                        # 12대 최신값 일괄 진단·우선순위
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from agent.loader import DataError, load_context
from agent.models import InputError, MeasurementInput
from agent.pipeline import latest_input, run_batch, run_diagnosis
from agent.report import blocks, output_dir, priority_rows, save_report, to_markdown, to_xlsx


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="펌프 이상진단 에이전트")
    p.add_argument("--pump", help="펌프ID (예: P-105)")
    p.add_argument("--discharge", type=float, help="토출압력 (bar)")
    p.add_argument("--flow", type=float, help="유량 (m³/h)")
    p.add_argument("--current", type=float, help="전류 (A)")
    p.add_argument("--vibration", type=float, help="진동RMS (mm/s)")
    p.add_argument("--suction", type=float, help="흡입압력 (bar, 선택·권장)")
    p.add_argument("--bearing-temp", type=float, help="베어링온도 (℃, 선택·권장)")
    p.add_argument("--time", help="측정일시 'YYYY-MM-DD HH:MM' (기본: 데이터 마지막 시각 다음)")
    p.add_argument("--signs", default="", help="현장 징후, 쉼표 구분 (씰 누수, 이상음(금속음), 자갈 소리, 커플링 발열, 모터 발열)")
    p.add_argument("--latest", action="store_true", help="선택 펌프의 마지막 운전값으로 진단")
    p.add_argument("--batch", action="store_true", help="전체 펌프 최신값 일괄 진단")
    p.add_argument("--no-llm", action="store_true", help="Claude API를 쓰지 않고 템플릿 의견 사용")
    p.add_argument("--no-save", action="store_true", help="보고서 파일을 저장하지 않음")
    p.add_argument("--data", help="데이터 엑셀 경로 (기본: config.yaml)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    a = parse_args(argv)
    try:
        ctx = load_context(data_path=a.data)
        if a.batch:
            reps = run_batch(ctx, use_llm=not a.no_llm)
            df = priority_rows(reps)
            print(df.drop(columns=["측정시각"]).to_string(index=False))
            if not a.no_save:
                path = output_dir(ctx) / f"priority_all_{datetime.now():%Y%m%d_%H%M}.xlsx"
                to_xlsx(reps, path)
                print(f"\n우선순위표 저장: {path}")
            return 0
        if not a.pump:
            print("오류: --pump 를 지정하세요. (--batch 는 전체 진단)", file=sys.stderr)
            return 2
        if a.latest:
            inp = latest_input(ctx, a.pump)
        else:
            missing = [n for n in ("discharge", "flow", "current", "vibration") if getattr(a, n) is None]
            if missing:
                print(f"오류: 필수 입력이 없습니다: {', '.join('--' + m for m in missing)}", file=sys.stderr)
                return 2
            when = datetime.strptime(a.time, "%Y-%m-%d %H:%M") if a.time else None
            signs = [s.strip() for s in a.signs.split(",") if s.strip()]
            inp = MeasurementInput(a.pump, a.discharge, a.flow, a.current, a.vibration, a.suction,
                                   a.bearing_temp, when, signs)
        rep = run_diagnosis(inp, ctx, use_llm=not a.no_llm)
        print(to_markdown(blocks(ctx, rep)))
        if not a.no_save:
            paths = save_report(ctx, rep)
            print("\n저장: " + ", ".join(str(p) for p in paths.values()))
        return 0
    except InputError as e:
        print(f"입력 오류: {e}", file=sys.stderr)
        return 2
    except DataError as e:
        print(f"데이터 오류: {e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"입력 오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
