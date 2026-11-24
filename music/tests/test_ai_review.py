"""후보 추천이 검토한 스냅샷 안에서만 해석되는지 확인한다."""

import json
import unittest
from dataclasses import asdict, replace

from core.ai_review import Candidate, Recommendation, ReviewCase, baseline_id, build_prompt, parse_reply


class AIReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.studio = Candidate("studio00001", '바람 "원곡"', "가수", "첫 앨범")
        self.live = Candidate("live0000002", "바람 (Live)", "가수", "서울 공연")
        self.case = ReviewCase("가수", '바람 "원곡"', "첫 앨범", (self.studio, self.live))
        self.reply = {"case_id": self.case.case_id, "video_id": self.studio.video_id, "reason": "요청한 원곡과 앨범이 일치합니다."}

    def test_fingerprint_binds_target_order_and_all_candidate_metadata(self) -> None:
        self.assertRegex(self.case.case_id, r"^[a-f0-9]{64}$")
        self.assertEqual(replace(self.case).case_id, self.case.case_id)
        for changed in (
            replace(self.case, artist="다른 가수"), replace(self.case, title="다른 곡"),
            replace(self.case, album="재발매"), replace(self.case, candidates=(self.live, self.studio)),
            replace(self.case, candidates=(self.studio,)),
            *(replace(self.case, candidates=(replace(self.studio, **field), self.live))
              for field in ({"video_id": "studio00002"}, {"artist": "그룹"}, {"title": "바람"}, {"album": "특별판"})),
        ):
            with self.subTest(case=changed):
                self.assertNotEqual(changed.case_id, self.case.case_id)
                with self.assertRaises(ValueError):
                    parse_reply(changed, json.dumps(self.reply))

    def test_case_rejects_ambiguous_and_incomplete_inputs(self) -> None:
        for invalid_id in ("", "too-short", "studio00001&list=x", " studio00001"):
            with self.subTest(video_id=invalid_id), self.assertRaises(ValueError):
                ReviewCase("가수", "곡", "", (Candidate(invalid_id, "곡"),))
        for title, candidates in [
            (" ", (self.studio,)), ("곡", ()), ("곡", (self.studio, replace(self.studio, title="다른 녹음"))),
        ]:
            with self.subTest(title=title, candidates=candidates), self.assertRaises(ValueError):
                ReviewCase("가수", title, "", candidates)
        with self.assertRaises(ValueError):
            ReviewCase("가수", "곡", "", (Candidate("studio00001", "   "),))

    def test_prompt_contains_only_the_target_and_every_candidate_as_data(self) -> None:
        candidates = tuple(Candidate(f"{i:011}", f'곡 "{i}"', "가수", "앨범") for i in range(200))
        case = replace(self.case, candidates=candidates)
        prompt = build_prompt(case)
        # 안내 문구에 답변 예시 JSON이 있어도 입력 스냅샷 자체를 검사한다.
        decoder = json.JSONDecoder()
        for offset, char in enumerate(prompt):
            if char == "{":
                try:
                    obj, _ = decoder.raw_decode(prompt, offset)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and "candidates" in obj:
                    break
        else:
            self.fail("검토 요청에 후보 JSON이 없습니다.")
        self.assertEqual(obj, {
            "case_id": case.case_id,
            "target": {"artist": case.artist, "title": case.title, "album": case.album},
            "candidates": [asdict(candidate) for candidate in candidates],
        })
        with self.assertRaises(ValueError):
            replace(case, candidates=candidates + (self.studio,))

    def test_recommendation_and_abstention_accept_plain_or_single_fenced_json(self) -> None:
        for selected in (self.studio.video_id, None):
            reply = dict(self.reply, video_id=selected)
            encoded = json.dumps(reply, ensure_ascii=False)
            for text in (encoded, f"```json\n{encoded}\n```", f"```\n{encoded}\n```"):
                with self.subTest(selected=selected, text=text):
                    self.assertEqual(parse_reply(self.case, text), Recommendation(selected, reply["reason"]))

    def test_reply_rejects_unknown_ids_extra_fields_and_malformed_json(self) -> None:
        valid = json.dumps(self.reply)
        malformed = [
            json.dumps(dict(self.reply, video_id="other000001")),
            json.dumps(dict(self.reply, video_id=False)),
            json.dumps(dict(self.reply, command="등록")),
            json.dumps({"case_id": self.case.case_id, "video_id": None}),
            valid.replace('"video_id":', '"video_id": null, "video_id":'),
            valid + valid, "설명\n" + valid, "```python\n" + valid + "\n```", "null",
        ]
        for text in malformed:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_reply(self.case, text)

    def test_reply_requires_short_single_line_korean_reason(self) -> None:
        for reason in ("", "   ", "Album matches", "가수 일치\n앨범 일치", "가" * 201, None):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                parse_reply(self.case, json.dumps(dict(self.reply, reason=reason)))
        self.assertEqual(parse_reply(self.case, json.dumps(dict(self.reply, reason="가" * 200))).reason, "가" * 200)

    def test_baseline_respects_instrumental_and_existing_confidence_cutoff(self) -> None:
        studio = replace(self.studio, title="바람")
        backing = replace(self.live, title="바람 (Inst.)")
        case = ReviewCase("가수", "바람 (Inst.)", "", (studio, backing))
        self.assertEqual(baseline_id(case), backing.video_id)
        self.assertIsNone(baseline_id(replace(case, candidates=(studio,))))
        self.assertIsNone(baseline_id(replace(case, artist="다른 사람", title="겨울 바다")))


if __name__ == "__main__":
    unittest.main()
