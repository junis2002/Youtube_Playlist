"""Compare paired human, baseline and model decisions from local JSONL review exports."""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


VIDEO_ID = re.compile(r"[0-9A-Za-z_-]{11}")
MINIMUM_CASES = 50
Engine = tuple[str, str]


@dataclass(frozen=True)
class Decision:
    pick: str | None
    source: str


@dataclass
class Case:
    candidate_ids: tuple[str, ...]
    source: str
    common: dict[str, Decision] = field(default_factory=dict)
    ai: dict[Engine, Decision] = field(default_factory=dict)


def unique_keys(pairs: list[tuple[str, object]]) -> dict:
    fields = {}
    for name, value in pairs:
        if name in fields:
            raise ValueError(f"같은 JSON 필드가 두 번 나왔습니다 ({name}).")
        fields[name] = value
    return fields


def required_text(row: dict, name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}에 비어 있지 않은 문자열이 필요합니다.")
    return value.strip()


def merge_record(row: dict, source: str, cases: dict[str, Case], engines: set[Engine]) -> None:
    if not isinstance(row, dict):
        raise ValueError("한 줄에 JSON 객체 하나가 필요합니다.")
    case_id = required_text(row, "case_id")
    ids = row.get("candidate_ids")
    if not isinstance(ids, list) or not all(isinstance(v, str) and VIDEO_ID.fullmatch(v) for v in ids):
        raise ValueError("candidate_ids에는 11자리 영상 ID 배열이 필요합니다.")
    if not 1 <= len(ids) <= 200 or len(set(ids)) != len(ids):
        raise ValueError("candidate_ids에 서로 다른 영상 ID를 1~200개 입력하세요.")
    candidates = tuple(ids)
    if case_id not in cases:
        cases[case_id] = Case(candidates, source)
    case = cases[case_id]
    if candidates != case.candidate_ids:
        raise ValueError(f"{case_id}의 후보 또는 순서가 {case.source}의 기록과 다릅니다.")

    engine = None
    if any(name in row for name in ("provider", "model", "ai")):
        engine = (required_text(row, "provider").lower(), required_text(row, "model"))
        engines.add(engine)

    for name in ("human", "baseline", "ai"):
        if name not in row:
            continue
        pick = row[name]
        if pick is not None and (not isinstance(pick, str) or pick not in candidates):
            raise ValueError(f"{case_id}의 {name} 값은 제공된 후보 ID 또는 null이어야 합니다.")
        if name == "ai":
            assert engine is not None
            decisions, key = case.ai, engine
        else:
            decisions, key = case.common, name
        previous = decisions.get(key)
        if previous is not None and previous.pick != pick:
            raise ValueError(f"{case_id}의 {key} 판정이 {previous.source}의 기록과 충돌합니다.")
        if previous is None:
            decisions[key] = Decision(pick, source)


def load_records(paths: list[Path]) -> tuple[dict[str, Case], set[Engine]]:
    cases: dict[str, Case] = {}
    engines: set[Engine] = set()
    for path in paths:
        with path.open(encoding="utf-8-sig") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                source = f"{path}:{number}"
                try:
                    merge_record(json.loads(line, object_pairs_hook=unique_keys), source, cases, engines)
                except ValueError as exc:
                    raise ValueError(f"{source} — {exc}") from exc
    return cases, engines


def outcomes(pairs: list[tuple[Decision, Decision]]) -> dict[str, int]:
    totals = {"correct_picks": 0, "wrong_picks": 0, "manual_reviews": 0,
              "wrong_picks_when_human_abstained": 0, "manual_when_human_picked": 0}
    for human, decision in pairs:
        if decision.pick is None:
            totals["manual_reviews"] += 1
            totals["manual_when_human_picked"] += int(human.pick is not None)
        elif decision.pick == human.pick:
            totals["correct_picks"] += 1
        else:
            totals["wrong_picks"] += 1
            totals["wrong_picks_when_human_abstained"] += int(human.pick is None)
    return totals


def compare(paths: list[Path]) -> dict:
    cases, engines = load_records(paths)
    labeled = [case for case in cases.values() if "human" in case.common]
    baseline_pairs = [(case.common["human"], case.common["baseline"]) for case in labeled
                      if "baseline" in case.common]
    groups = []
    for engine in sorted(engines):
        paired = [case for case in labeled if "baseline" in case.common and engine in case.ai]
        groups.append({
            "provider": engine[0], "model": engine[1], "paired_cases": len(paired),
            "sample_threshold_met": len(paired) >= MINIMUM_CASES,
            "ai_unavailable_on_labeled": sum(engine not in case.ai for case in labeled),
            "baseline": outcomes([(case.common["human"], case.common["baseline"]) for case in paired]),
            "ai": outcomes([(case.common["human"], case.ai[engine]) for case in paired]),
        })
    return {"distinct_cases": len(cases), "human_labeled": len(labeled),
            "unlabeled": len(cases) - len(labeled), "minimum_paired_cases": MINIMUM_CASES,
            "baseline_unavailable_on_labeled": len(labeled) - len(baseline_pairs),
            "baseline_on_labeled": outcomes(baseline_pairs), "models": groups}


def display_counts(summary: dict) -> str:
    return (f"정답 선택 {summary['correct_picks']} · 잘못 선택 {summary['wrong_picks']} "
            f"(사람이 보류한 사례 {summary['wrong_picks_when_human_abstained']}) · "
            f"수동 검토 {summary['manual_reviews']} (사람이 후보를 고른 사례 {summary['manual_when_human_picked']})")


def main() -> int:
    parser = argparse.ArgumentParser(description="JSONL의 독립적인 사람 판정과 같은 후보에 대한 기준선·AI 선택을 비교합니다.",
                                     epilog="null은 명시적인 보류이며 생략된 판정은 미확인입니다. 비교는 모델별로 집계합니다.")
    parser.add_argument("files", metavar="FILE", type=Path, nargs="+", help="앱에서 내보낸 UTF-8 JSONL")
    parser.add_argument("--json", action="store_true", help="구조화한 JSON으로 출력")
    args = parser.parse_args()
    try:
        report = compare(args.files)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0

    print(f"고유 사례 {report['distinct_cases']} · 사람 판정 {report['human_labeled']} · 미판정 {report['unlabeled']}")
    print(f"사람 판정 중 기준선 미기록 {report['baseline_unavailable_on_labeled']}")
    print("판정이 있는 전체 기준선  " + display_counts(report["baseline_on_labeled"]))
    if not report["models"]:
        print("모델이 기록된 사례가 없습니다.")
    for model in report["models"]:
        result = "도달" if model["sample_threshold_met"] else "미달"
        print(f"\n{model['provider']} / {model['model']} · 짝지은 고유 사례 {model['paired_cases']} · "
              f"{MINIMUM_CASES}건 기준 {result}")
        print(f"사람 판정 중 이 모델의 추천 미기록 {model['ai_unavailable_on_labeled']}")
        print("  같은 사례의 기준선  " + display_counts(model["baseline"]))
        print("  같은 사례의 AI      " + display_counts(model["ai"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
