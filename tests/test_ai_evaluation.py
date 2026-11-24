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

    def test_audit_matches_original_replies_without_model_pairs(self):
        ids = ["a" * 64, "b" * 64, "c" * 64]
        labels = self.save("사람.jsonl", [case(ids[0], human=None, baseline=A),
                                        case(ids[1], human=B, baseline=B), case(ids[2], human=A)])
        first = self.folder / "보류.txt"
        second = self.folder / "추천.json"
        reason = "원문에만 있는 설명"
        raw = json.dumps({"case_id": ids[0], "video_id": None, "reason": reason}, ensure_ascii=False)
        first.write_text("```json\n" + raw + "\n```", encoding="utf-8-sig")
        copied = self.folder / "복사본.json"
        copied.write_text(raw, encoding="utf-8")
        second.write_text(json.dumps({"case_id": ids[1], "video_id": A, "reason": reason}), encoding="utf-8")
        args = [sys.executable, str(CLI), str(labels), "--audit-reply", str(first),
                "--audit-reply", str(first), "--audit-reply", str(copied), "--audit-reply", str(second)]
        proc = subprocess.run([*args, "--json"], capture_output=True, text=True, check=True)
        actual = json.loads(proc.stdout)
        audit = actual.pop("reply_audit")
        self.assertEqual(actual, compare([labels]))
        self.assertEqual(actual["models"], [])
        self.assertEqual((audit["compared"], audit["matched"], audit["differing"]), (2, 1, 1))
        self.assertEqual(audit["human_without_reply"], [ids[2]])
        self.assertEqual(audit["observations"], [
            {"case_id": ids[0], "files": [str(first), str(copied)], "human": None, "observed": None, "match": True},
            {"case_id": ids[1], "files": [str(second)], "human": B, "observed": A, "match": False},
        ])
        text = subprocess.check_output(args, text=True)
        self.assertIn("일치 1 · 다름 1", text)
        self.assertNotIn(reason, text)

    def test_audit_rejects_missing_or_ambiguous_evidence(self):
        key = "a" * 64
        labels = self.save("labels.jsonl", [case(key, human=A), case("b" * 64, baseline=A)])
        reply = self.folder / "original.json"
        valid = {"case_id": key, "video_id": B, "reason": "가수와 앨범 일치"}
        for field, bad in [("case_id", key[:16]), ("case_id", "c" * 64), ("case_id", "b" * 64),
                           ("video_id", "other000001"), ("reason", "line one\n줄 둘"), ("reason", "English only")]:
            with self.subTest(field=field, bad=bad):
                reply.write_text(json.dumps({**valid, field: bad}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "original.json"):
                    compare([labels], reply_files=[reply])
        duplicate = json.dumps(valid).replace('"video_id":', '"video_id":null, "video_id":')
        reply.write_text(duplicate, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "original.json"):
            compare([labels], reply_files=[reply])
        reply.write_text(json.dumps(valid), encoding="utf-8")
        changed = self.folder / "changed.json"
        changed.write_text(json.dumps({**valid, "video_id": A}), encoding="utf-8")
        result = subprocess.run([sys.executable, str(CLI), str(labels), "--json", "--audit-reply", str(reply),
                                 "--audit-reply", str(changed)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"changed.json", result.stderr)
        self.assertIn(b"original.json", result.stderr)
        self.assertEqual(result.stdout, b"")
        padded = self.save("padded.jsonl", [case(" " + key, human=A)])
        with self.assertRaisesRegex(ValueError, "padded.jsonl:1"):
            compare([padded], reply_files=[reply])
