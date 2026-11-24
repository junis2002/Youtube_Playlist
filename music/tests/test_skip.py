"""건너뛰기·복원이 곡 상태, 후보와 기존 등록 이력을 보존하는지 검증한다."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.checkpoint import Checkpoint
from core.playlist import register


class SkipTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "음원 작업.sqlite3"
        self.store = Checkpoint(self.path)
        self.job = self.store.create("목록", [("가수", "원곡", "앨범"), ("가수", "둘째 곡", "")])
        self.video_id = "studio00001"
        self.candidates = [{"videoId": self.video_id, "artist": "가수", "title": "원곡", "score": 96}]

    def test_before_search_and_low_without_candidates_can_be_skipped_and_restored(self) -> None:
        self.store.result(self.job, 2, "Low", [], query="수정한 검색어")
        before = [dict(r) for r in self.store.tracks(self.job)]
        for seq in (1, 2):
            self.store.set_skipped(self.job, seq, skipped=True)
        reopened = Checkpoint(self.path)
        self.assertEqual([dict(r) for r in reopened.tracks(self.job)],
                         [dict(r, delivery="skipped") for r in before])
        for seq in (1, 2):
            reopened.set_skipped(self.job, seq, skipped=False)
        self.assertEqual([dict(r) for r in self.store.tracks(self.job)], before)

    def test_skipped_confirmed_track_keeps_selection_across_stale_edits_and_registration(self) -> None:
        self.store.result(self.job, 1, "High", self.candidates, query="조회한 원곡")
        self.store.destination(self.job, "PLtest")
        before = dict(self.store.tracks(self.job)[0])
        self.store.set_skipped(self.job, 1, skipped=True)
        old_tab = Checkpoint(self.path)
        old_tab.result(self.job, 1, "Not Found", [], query="오래된 검색 결과")
        old_tab.choose(self.job, 1, "other000001")
        with self.assertRaises(ValueError):
            old_tab.choose_album_track(self.job, 1, "other000001", "다른 곡", "다른 채널")
        api = Mock(spec_set=["get_playlist", "add_playlist_items"])
        progress = Mock()
        register(api, old_tab, self.job, progress, delay=0)
        self.assertEqual(api.mock_calls, [])
        progress.assert_not_called()
        self.assertEqual(dict(old_tab.tracks(self.job)[0]), dict(before, delivery="skipped"))
        old_tab.set_skipped(self.job, 1, skipped=False)
        self.assertEqual(dict(old_tab.tracks(self.job)[0]), before)

    def test_sent_rows_and_missing_rows_reject_changes(self) -> None:
        self.store.delivered(self.job, [("added", 1), ("existing", 2)])
        before = [dict(r) for r in self.store.tracks(self.job)]
        for seq in (1, 2, 99):
            for skip in (True, False):
                with self.subTest(seq=seq, skip=skip), self.assertRaises(ValueError):
                    self.store.set_skipped(self.job, seq, skipped=skip)
        self.assertEqual([dict(r) for r in self.store.tracks(self.job)], before)

    def test_skipping_respects_existing_job_lock(self) -> None:
        with self.store.lock(self.job), self.assertRaises(ValueError):
            Checkpoint(self.path).set_skipped(self.job, 1, skipped=True)
        self.assertEqual(self.store.tracks(self.job)[0]["delivery"], "pending")
        self.store.set_skipped(self.job, 1, skipped=True)
        self.assertEqual(self.store.tracks(self.job)[0]["delivery"], "skipped")


if __name__ == "__main__":
    unittest.main()
