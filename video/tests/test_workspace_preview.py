"""저장된 영상 작업을 통합 화면에서 열어 등록 전에 보이는 정보를 검증한다."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

# video 디렉터리에서 단독 테스트해도 루트 앱과 같은 패키지 경로로 불러온다.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from video import app
from video.core.playlist import PlaylistError, PlaylistInfo


class PreviewScreen(unittest.TestCase):
    def test_metadata_is_read_once_and_follows_the_active_account_and_target(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            token_file = root / "token.json"
            token_file.write_text("test-session", encoding="utf-8")
            db = app.Store(root / "data" / "checkpoint.sqlite3")
            job = db.new_job("이전 작업", [("원곡", "v0000000001")], {})
            old = PlaylistInfo("PL-EXISTING", "기존 목록", "private")
            resumed = PlaylistInfo("PL-RESUME", "저장된 대상", "unlisted")
            with (patch.object(app, "ROOT", root), patch.object(app, "get_service", return_value=object()),
                  patch.object(app.Playlists, "channel_title", side_effect=["첫 채널", "바뀐 채널"]) as channel,
                  patch.object(app.Playlists, "mine_info", return_value={old.id: old}) as mine,
                  patch.object(app.Playlists, "playlist_info", side_effect=[resumed, PlaylistError("조회 권한 확인")]) as info,
                  patch.object(app, "publish") as publish):
                at = AppTest.from_string("from video.app import workspace\nworkspace()").run()
                self.assertEqual(at.selectbox(key="video_job_id").value, job)
                self.assertEqual(at.radio(key="video_mode").value, "빠른 링크 (무인증)")
                channel.assert_not_called()

                at.radio(key="video_mode").set_value("내 계정에 등록 (공식 API)").run()
                at.text_input(key=f"title-{job}").set_value("새 공개 목록").run()
                at.selectbox(key=f"privacy-{job}").set_value("public").run()
                self.assertIn("새 공개 목록", at.text[0].value)
                self.assertIn("첫 채널", at.text[0].value)
                self.assertIn("public", at.text[0].value)
                self.assertIn("등록 대기  1개", at.text[0].value)
                channel.assert_called_once()
                at.radio(key=f"dest-{job}-False").set_value("기존 목록에 이어붙이기").run()
                next(b for b in at.button if b.label == "내 재생목록 불러오기").click().run()
                self.assertIn("기존 목록", at.text[0].value)
                self.assertIn("private", at.text[0].value)
                mine.assert_called_once()
                info.assert_not_called()

                db.set_target(job, resumed.id)
                at.run()
                self.assertIn("저장된 대상", at.text[0].value)
                self.assertIn("unlisted", at.text[0].value)
                at.run()
                info.assert_called_once_with(resumed.id)
                token_file.write_text("different-test-session", encoding="utf-8")
                at.run()
                self.assertIn("바뀐 채널", at.text[0].value)
                self.assertNotIn("저장된 대상", at.text[0].value)
                self.assertIn("공개 범위  확인 불가", at.text[0].value)
                self.assertIn("조회 권한 확인", [w.value for w in at.warning])
                at.run()
                self.assertEqual(channel.call_count, 2)
                self.assertEqual(info.call_count, 2)
                self.assertFalse(at.exception)
                publish.assert_not_called()
