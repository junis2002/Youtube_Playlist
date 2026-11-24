"""로컬 JSONL을 읽는 비교 도구의 중복·충돌·보류·모델 분리 회귀 테스트."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.evaluate_ai_reviews import compare


A, B = "track000001", "track000002"
CLI = Path(__file__).resolve().parents[1] / "tools/evaluate_ai_reviews.py"


def case(key, **fields):
    return {"case_id": key, "candidate_ids": [A, B], **fields}


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def save(self, name, rows):
        path = self.folder / name
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8-sig")
        return path

    def test_paired_metrics_keep_abstentions_and_model_groups_separate(self):
        references = self.save("사람.jsonl", [
            case("1", human=A, baseline=B),
            case("2", human=None, baseline=None),
            case("3", human=B, baseline=B),
            case("4", human=A),
            case("5", baseline=A),
        ])
        replies = []
        for key, answer in [("1", A), ("2", B), ("3", None), ("4", A), ("5", A)]:
            replies.append(case(key, ai=answer, provider="ollama", model="model-1"))
        replies.extend([case("1", ai=None, provider="codex", model="model-1"),
                        case("1", ai=B, provider="ollama", model="model-2")])
        models = self.save("모델.jsonl", replies)
        result = compare([references, models, models])
        self.assertEqual((result["distinct_cases"], result["human_labeled"], result["unlabeled"]), (5, 4, 1))
        self.assertEqual(result["baseline_unavailable_on_labeled"], 1)
        groups = {(g["provider"], g["model"]): g for g in result["models"]}
        local = groups[("ollama", "model-1")]
        self.assertEqual(local["paired_cases"], 3)
        self.assertFalse(local["sample_threshold_met"])
        self.assertEqual(local["ai"], {"correct_picks": 1, "wrong_picks": 1, "manual_reviews": 1,
                                       "wrong_picks_when_human_abstained": 1, "manual_when_human_picked": 1})
        self.assertEqual(local["baseline"], {"correct_picks": 1, "wrong_picks": 1, "manual_reviews": 1,
                                             "wrong_picks_when_human_abstained": 0, "manual_when_human_picked": 0})
        self.assertEqual(groups[("codex", "model-1")]["paired_cases"], 1)
        self.assertEqual(groups[("codex", "model-1")]["ai_unavailable_on_labeled"], 3)
        self.assertEqual(groups[("ollama", "model-2")]["ai"]["wrong_picks"], 1)

    def test_no_runs_or_labels_are_inferred(self):
        path = self.save("blank.jsonl", [case("s1", provider="ollama", model="test", baseline=None),
                                         case("s2", human=None)])
        report = compare([path])
        self.assertEqual(report["human_labeled"], 1)
        self.assertEqual(report["baseline_unavailable_on_labeled"], 1)
        group, = report["models"]
        self.assertEqual((group["paired_cases"], group["ai_unavailable_on_labeled"]), (0, 1))
        self.assertEqual(group["ai"]["manual_reviews"], 0)

    def test_repeated_snapshots_never_make_49_distinct_pairs_eligible(self):
        # Synthetic counts verify the threshold, without including test fixtures in any real export.
        def row(i):
            return case(str(i), human=A, baseline=A, ai=A, provider="ollama", model="fixture")
        near = self.save("near.jsonl", [row(i) for i in range(49)])
        self.assertFalse(compare([near, near])["models"][0]["sample_threshold_met"])
        last = self.save("last.jsonl", [row(49)])
        result = subprocess.run([sys.executable, str(CLI), "--json", str(near), str(last)],
                                text=True, capture_output=True, check=True)
        group, = json.loads(result.stdout)["models"]
        self.assertTrue(group["sample_threshold_met"])
        self.assertEqual(group["paired_cases"], 50)

    def test_conflicting_snapshots_are_rejected_with_file_and_line(self):
        original = case("x", human=A, baseline=None, ai=B, provider="ollama", model="m")
        for key, changed in [("human", None), ("baseline", A), ("ai", A), ("candidate_ids", [A]), ("candidate_ids", [B, A])]:
            with self.subTest(key=key):
                path = self.save("conflict.jsonl", [original, {**original, key: changed}])
                with self.assertRaisesRegex(ValueError, r"conflict\.jsonl:2.*conflict\.jsonl:1"):
                    compare([path])

    def test_candidate_bounds_and_uniqueness_match_app_exports(self):
        unique = [f"v{i:010}" for i in range(201)]
        for ids in ([], [A, A], unique):
            with self.subTest(count=len(ids)):
                path = self.save("malformed.jsonl", [case("x", candidate_ids=ids)])
                with self.assertRaisesRegex(ValueError, "candidate_ids"):
                    compare([path])
        for count in (1, 200):
            path = self.save("valid.jsonl", [case("x", candidate_ids=unique[:count])])
            self.assertEqual(compare([path])["distinct_cases"], 1)

    def test_invalid_records_do_not_produce_partial_cli_report(self):
        for invalid in [case("x", human="other000001"), case("x", ai=None),
                        case("x", model="m"), case("x", candidate_ids=["bad"])]:
            with self.subTest(record=invalid):
                path = self.save("bad.jsonl", [case("valid", human=A), invalid])
                proc = subprocess.run([sys.executable, str(CLI), str(path), "--json"], capture_output=True)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, b"")
                self.assertIn(b"bad.jsonl:2", proc.stderr)
