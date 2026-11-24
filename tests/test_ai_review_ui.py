"""후보 추천·보류·검증 오류가 사용자의 실제 곡 선택에 미치는 영향을 확인한다."""

import json
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from tools.evaluate_ai_reviews import compare


APP = """
import streamlit as st
from music.ai_review_ui import show_ai_review
from music.core.ai_review import Candidate, ReviewCase

title = st.text_input("대상 곡명", "바람")
options = (
    Candidate("studio00001", "바람", "가수", "봄"),
    Candidate("live0000002", "바람 (Live)", "가수", "겨울 공연"),
)
case = ReviewCase("가수", title, "봄", options)
st.session_state.fixture_case_id = case.case_id
show_ai_review(case, context="test_review")
st.selectbox("내가 선택한 수록곡", [c.video_id for c in options], index=None, key="actual_choice")
"""

MISSING = [name for name in ("streamlit", "rapidfuzz", "ytmusicapi") if find_spec(name) is None]


@unittest.skipIf(MISSING, "앱 의존성 미설치: " + ", ".join(MISSING))
class CandidateReviewScreenTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest

        self.page = AppTest.from_string(APP, default_timeout=15)
        self.run_page()

    def run_page(self):
        self.page.run()
        self.assertFalse(self.page.exception, [e.value for e in self.page.exception])

    def submit(self, video_id, reason, case_id=None):
        reply = {"case_id": case_id or self.page.session_state.fixture_case_id,
                 "video_id": video_id, "reason": reason}
        next(w for w in self.page.text_area if w.label == "AI 응답 JSON").set_value(json.dumps(reply, ensure_ascii=False))
        self.run_page()
        next(b for b in self.page.button if b.label == "응답 검증").click()
        self.run_page()

    def messages(self):
        return "\n".join(str(item.value) for kind in ("text", "info", "success", "warning", "error", "markdown", "caption")
                         for item in self.page.get(kind))

    def downloads(self, folder=None):
        import streamlit as st

        with patch.object(st, "download_button", wraps=st.download_button) as download:
            self.run_page()
        records = {}
        for call in download.call_args_list:
            label, data, filename = call.args
            text = data.decode("utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(len(text.splitlines()), 1)
            self.assertEqual(call.kwargs["mime"], "application/json" if filename.endswith(".json") else "application/x-ndjson")
            records[label] = (filename, json.loads(text))
            if folder is not None:
                (folder / filename).write_bytes(data)
        return records

    def test_recommendation_and_abstention_preserve_manual_selection(self):
        self.assertTrue(any(e.label == "AI 후보 검토" for e in self.page.expander))
        self.assertIsNone(self.page.selectbox(key="actual_choice").value)
        self.submit("studio00001", "가수와 앨범이 일치합니다.")
        self.assertIn("가수와 앨범이 일치합니다.", self.messages())
        self.assertIsNone(self.page.selectbox(key="actual_choice").value)
        self.assertFalse(any(w.label == "평가용 내 판정" for w in self.page.selectbox))
        self.assertEqual(set(self.downloads()), {"관찰 응답 JSON 다운로드"})
        self.page.selectbox(key="actual_choice").set_value("live0000002")
        self.submit(None, "같은 버전인지 확인하기 어렵습니다.")
        self.assertIn("같은 버전인지 확인하기 어렵습니다.", self.messages())
        self.assertNotIn("가수와 앨범이 일치합니다.", self.messages())
        self.assertEqual(self.page.selectbox(key="actual_choice").value, "live0000002")
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        case_id = self.page.session_state.fixture_case_id
        filename = f"ai-observation-{case_id[:16]}.json"
        self.assertEqual(self.downloads(folder), {"관찰 응답 JSON 다운로드": (filename, {
            "case_id": case_id, "video_id": None, "reason": "같은 버전인지 확인하기 어렵습니다.",
        })})
        with self.assertRaisesRegex(ValueError, "candidate_ids"):
            compare([folder / filename])

    def test_changed_case_and_invalid_candidate_cannot_reuse_a_recommendation(self):
        case_id = self.page.session_state.fixture_case_id
        self.submit("studio00001", "첫 요청의 앨범과 일치합니다.")
        self.assertIn("첫 요청의 앨범과 일치합니다.", self.messages())
        self.submit("extra000003", "후보에 없는 음원입니다.")
        self.assertIn("현재 검토 후보에 없습니다", self.messages())
        self.assertNotIn("첫 요청의 앨범과 일치합니다.", self.messages())
        self.assertFalse(self.downloads())
        self.submit("studio00001", "첫 곡의 스튜디오 버전입니다.")
        self.assertIn("첫 곡의 스튜디오 버전입니다.", self.messages())
        next(w for w in self.page.text_input if w.label == "대상 곡명").set_value("새 요청")
        self.run_page()
        self.assertNotEqual(self.page.session_state.fixture_case_id, case_id)
        self.assertNotIn("첫 곡의 스튜디오 버전입니다.", self.messages())
        self.assertFalse(self.downloads())
        self.submit("studio00001", "이전 요청에 대한 추천입니다.", case_id=case_id)
        self.assertIn("다른 곡 또는 다른 후보 목록", self.messages())
        self.assertNotIn("이전 요청에 대한 추천입니다.", self.messages())
        self.assertIsNone(self.page.selectbox(key="actual_choice").value)

    def test_evaluation_requires_an_independent_judgment_and_model_identity(self):
        case_id = self.page.session_state.fixture_case_id
        self.assertEqual(self.downloads(), {})
        human = next(w for w in self.page.selectbox if w.label == "평가용 내 판정")
        self.assertIsNone(human.value)
        human.set_value("live0000002")
        self.run_page()
        next(b for b in self.page.button if b.label == "내 판정 기록").click()
        self.run_page()
        self.assertIn("평가용 내 판정 · 바람 (Live)", self.messages())
        reference = {"case_id": case_id, "candidate_ids": ["studio00001", "live0000002"],
                     "human": "live0000002", "baseline": "studio00001"}
        independent = {"내 판정 JSONL 다운로드": (f"ai-review-{case_id[:16]}-human.jsonl", reference)}
        self.assertEqual(self.downloads(), independent)
        self.submit("studio00001", "앨범 수록 원곡과 일치합니다.")
        self.assertEqual(self.downloads(), {**independent, "관찰 응답 JSON 다운로드": (
            f"ai-observation-{case_id[:16]}.json",
            {"case_id": case_id, "video_id": "studio00001", "reason": "앨범 수록 원곡과 일치합니다."},
        )})
        next(w for w in self.page.text_input if w.label == "Codex 모델 이름").set_value("test-model")
        self.run_page()
        self.assertEqual(self.downloads(), independent)
        self.submit("studio00001", "새 실행에서 요청한 가수와 앨범이 일치합니다.")
        paired = {**reference, "model": "test-model", "provider": "codex", "ai": "studio00001"}
        self.assertEqual(self.downloads(), {
            **independent, "평가 사례 JSONL 다운로드": (f"ai-review-{case_id[:16]}.jsonl", paired),
        })
        self.assertIsNone(self.page.selectbox(key="actual_choice").value)

    def test_named_model_export_joins_saved_human_label_across_sessions(self):
        from streamlit.testing.v1 import AppTest

        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        human = next(w for w in self.page.selectbox if w.label == "평가용 내 판정")
        human.set_value("live0000002").run()
        next(b for b in self.page.button if b.label == "내 판정 기록").click().run()
        human_filename, _ = self.downloads(folder)["내 판정 JSONL 다운로드"]

        self.page = AppTest.from_string(APP, default_timeout=15).run()
        next(b for b in self.page.button if b.label == "Codex 요청 보기").click().run()
        prompt = json.loads(self.page.code[0].value.splitlines()[-1])
        case_id = prompt["case_id"]
        model_input = next(w for w in self.page.text_input if w.label == "Codex 모델 이름")
        model_input.set_value("테스트/모델:v1").run()
        self.submit("live0000002", "후보의 가수와 제목이 일치합니다.", case_id=case_id)
        exports = self.downloads(folder)
        self.assertEqual(set(exports), {"모델 응답 JSONL 다운로드"})
        filename, model_record = exports["모델 응답 JSONL 다운로드"]
        self.assertRegex(filename, rf"^ai-review-{case_id[:16]}-model-[0-9a-f]{{16}}\.jsonl$")
        self.assertEqual(model_record, {
            "case_id": case_id, "candidate_ids": ["studio00001", "live0000002"],
            "provider": "codex", "model": "테스트/모델:v1", "ai": "live0000002",
        })
        result = compare([folder / human_filename, folder / filename, folder / filename])
        self.assertEqual((result["distinct_cases"], result["human_labeled"]), (1, 1))
        group, = result["models"]
        self.assertEqual((group["paired_cases"], group["ai"]["correct_picks"]), (1, 1))
        self.assertEqual(compare([folder / filename])["models"][0]["paired_cases"], 0)

        next(w for w in self.page.text_input if w.label == "Codex 모델 이름").set_value("테스트/모델:v2").run()
        self.assertFalse(self.downloads())
        self.submit(None, "음원의 버전을 구분할 근거가 충분하지 않습니다.")
        second_filename, second_record = self.downloads(folder)["모델 응답 JSONL 다운로드"]
        self.assertNotEqual(second_filename, filename)
        self.assertIsNone(second_record["ai"])
        self.assertEqual([group["paired_cases"] for group in compare(list(folder.glob("*.jsonl")))["models"]], [1, 1])
        self.assertIsNone(self.page.selectbox(key="actual_choice").value)

    def test_human_abstention_can_be_exported_before_running_a_model(self):
        from streamlit.testing.v1 import AppTest

        next(w for w in self.page.selectbox if w.label == "평가용 내 판정").set_value("")
        self.run_page()
        next(b for b in self.page.button if b.label == "내 판정 기록").click()
        self.run_page()
        case_id = self.page.session_state.fixture_case_id
        self.assertEqual(self.downloads(), {"내 판정 JSONL 다운로드": (f"ai-review-{case_id[:16]}-human.jsonl", {
            "case_id": case_id, "candidate_ids": ["studio00001", "live0000002"], "human": None, "baseline": "studio00001",
        })})

        # 같은 후보를 제시하면 사람 판정이 없는 세션과 모델 요청 전체가 일치한다.
        unlabeled = AppTest.from_string(APP, default_timeout=15).run()
        for page in (self.page, unlabeled):
            next(b for b in page.button if b.label == "Codex 요청 보기").click().run()
        self.assertEqual(self.page.code[0].value, unlabeled.code[0].value)

    def test_unavailable_local_models_do_not_display_another_providers_response(self):
        self.submit("studio00001", "앨범의 원곡입니다.")
        with patch("music.core.local_model.list_models", return_value=[]) as list_models:
            next(w for w in self.page.radio if w.label == "추천을 받을 곳").set_value("로컬 Ollama")
            self.run_page()
            list_models.assert_not_called()
            self.assertNotIn("앨범의 원곡입니다.", self.messages())
            self.assertFalse(self.downloads())
            next(b for b in self.page.button if b.label == "설치된 모델 확인").click()
            self.run_page()
            list_models.assert_called_once_with()
            self.assertIn("설치된 로컬 채팅 모델이 없습니다", self.messages())
            self.assertIsNone(self.page.selectbox(key="actual_choice").value)

    def test_changing_the_selected_ollama_model_clears_its_export(self):
        from music.core.ai_review import Recommendation

        next(w for w in self.page.radio if w.label == "추천을 받을 곳").set_value("로컬 Ollama").run()
        with patch("music.core.local_model.list_models", return_value=["fixture-a", "fixture-b"]):
            next(b for b in self.page.button if b.label == "설치된 모델 확인").click().run()
        with patch("music.core.local_model.recommend", return_value=Recommendation("studio00001", "앨범이 일치합니다.")):
            next(b for b in self.page.button if b.label == "이 모델로 추천 받기").click().run()
        _, record = self.downloads()["모델 응답 JSONL 다운로드"]
        self.assertEqual((record["provider"], record["model"], record["ai"]), ("ollama", "fixture-a", "studio00001"))
        next(w for w in self.page.selectbox if w.label == "설치된 로컬 모델").set_value("fixture-b").run()
        self.assertFalse(self.downloads())
        self.assertIsNone(self.page.selectbox(key="actual_choice").value)

    def test_opening_the_model_prompt_closes_independent_human_labeling(self):
        self.assertFalse(self.page.code)
        self.assertTrue(any(w.label == "평가용 내 판정" for w in self.page.selectbox))
        next(b for b in self.page.button if b.label == "Codex 요청 보기").click()
        self.run_page()
        self.assertFalse(any(w.label == "평가용 내 판정" for w in self.page.selectbox))
        self.assertFalse(any(b.label == "내 판정 기록" for b in self.page.button))
        self.assertIn(self.page.session_state.fixture_case_id, self.page.code[0].value)
        self.assertIsNone(self.page.selectbox(key="actual_choice").value)
