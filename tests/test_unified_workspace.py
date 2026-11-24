"""두 기존 DB와 공개 앨범 → 공식 API → 영상 작업 경로를 계정 없이 검증한다."""

import subprocess
import sys
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
APP_MODULES = ("streamlit", "ytmusicapi", "yt_dlp", "googleapiclient", "google_auth_oauthlib",
               "filelock", "rapidfuzz", "pandas", "openpyxl")
MISSING = [name for name in APP_MODULES if find_spec(name) is None]


@unittest.skipIf(MISSING, "통합 앱 의존성 미설치: " + ", ".join(MISSING))
class UnifiedWorkspaceTests(unittest.TestCase):
    def setUp(self):
        from music import app as music
        from streamlit.testing.v1 import AppTest
        from video import app as video

        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(music, "ROOT", folder / "music"))
        self.enterContext(patch.object(music, "AUTH_FILE", folder / "music/browser.json"))
        self.enterContext(patch.object(video, "ROOT", folder / "video"))
        self.music_db = music.ROOT / ".data/music.sqlite3"
        self.video_db = video.ROOT / "data/checkpoint.sqlite3"
        self.new_session = lambda: AppTest.from_file(str(ROOT / "app.py"), default_timeout=15)
        self.music = music
        self.video = video

    def run_page(self, page):
        page.run()
        self.assertFalse(page.exception, [e.value for e in page.exception])
        self.assertFalse(page.error, [e.value for e in page.error])

    def test_both_legacy_databases_reopen_in_the_unified_app(self):
        # Each former standalone entry point used its own top-level core package in its own process.
        seed_music = """
from pathlib import Path
import sys
from core.checkpoint import Checkpoint
db = Checkpoint(Path(sys.argv[1]))
print(db.create("기존 음원 작업", [("기존 가수", "기존 곡", "기존 앨범")]))
"""
        seed_video = """
from pathlib import Path
import sys
from core.checkpoint import Store
db = Store(Path(sys.argv[1]))
print(db.new_job("기존 영상 작업", [("https://youtu.be/old00000001", "old00000001")], {}))
# The pre-unification database has no transfer history table.
with db.connect() as conn:
    conn.execute("DROP TABLE IF EXISTS confirmed_imports")
"""
        music_id = subprocess.check_output([sys.executable, "-c", seed_music, str(self.music_db)],
                                           cwd=ROOT / "music", text=True).strip()
        video_id = int(subprocess.check_output([sys.executable, "-c", seed_video, str(self.video_db)],
                                              cwd=ROOT / "video", text=True))
        page = self.new_session()
        self.run_page(page)
        self.assertEqual(page.selectbox(key="video_job_id").value, video_id)
        self.assertIn("기존 영상 작업", page.selectbox(key="video_job_id").options[0])
        page.radio(key="workspace_view").set_value("음원 목록")
        self.run_page(page)
        self.assertEqual(page.selectbox(key="music_job").value, music_id)
        self.assertIn("기존 음원 작업", page.selectbox(key="music_job").options[0])
        self.assertEqual(self.video.Store(self.video_db).items(video_id)[0]["video"], "old00000001")

    def mock_album_services(self):
        album_id = "OLAK5uy_TestAlbum12"
        videos = ["first000001", "final000002"]
        public = Mock(spec_set=["search", "get_album"])
        public.search.return_value = [{"resultType": "album", "title": "테스트 앨범", "artist": "테스트 가수",
                                       "browseId": "MPREb_testalbum", "playlistId": album_id}]
        public.get_album.return_value = {"title": "테스트 앨범", "tracks": [], "audioPlaylistId": album_id}
        self.enterContext(patch("music.core.public_album.YTMusic", return_value=public))

        # The service double permits only listing, so unexpected write operations fail.
        service = Mock(spec_set=["playlistItems"])
        items = Mock(spec_set=["list"])
        service.playlistItems.return_value = items
        items.list.return_value.execute.return_value = {"items": [
            {"id": "item0000001", "contentDetails": {"videoId": vid},
             "snippet": {"title": title, "position": i, "videoOwnerChannelTitle": "테스트 가수"}}
            for i, (vid, title) in enumerate(zip(videos, ["버전 하나", "사용자가 고른 버전"], strict=True))
        ]}
        self.enterContext(patch.object(self.video, "get_service", return_value=service))

        return album_id, videos, public, items

    def select_second_album_track(self, page):
        next(b for b in page.button if b.label == "앨범 검색").click()
        self.run_page(page)
        page.selectbox(key="public_album_choice").set_value(0)
        self.run_page(page)
        next(b for b in page.button if b.label == "선택한 앨범의 트랙 불러오기").click()
        self.run_page(page)
        next(b for b in page.button if b.label == "기존 Google 연결로 수록곡 조회").click()
        self.run_page(page)
        self.assertIsNone(page.selectbox(key="album_export_choice").value)
        page.selectbox(key="album_export_choice").set_value(1)
        self.run_page(page)
        page.button(key="video_album_choose").click()
        self.run_page(page)

    def test_selected_service_track_creates_one_local_video_job(self):
        album_id, videos, public, items = self.mock_album_services()
        page = self.new_session()
        self.run_page(page)
        page.radio(key="workspace_view").set_value("앨범 수록곡")
        self.run_page(page)
        page.text_input(key="public_album_artist").set_value("테스트 가수")
        page.text_input(key="public_album_title").set_value("테스트 앨범")
        self.select_second_album_track(page)
        self.assertEqual(page.radio(key="workspace_view").value, "영상 목록")
        job = page.selectbox(key="video_job_id").value
        store = self.video.Store(self.video_db)
        rows = store.items(job)
        self.assertEqual([(r["video"], r["registration"]) for r in rows], [(videos[1], "pending")])
        self.assertIsNone(store.job(job)["playlist"])
        self.run_page(page)
        self.run_page(self.new_session())
        self.assertEqual([j["id"] for j in store.jobs()], [job])
        self.assertEqual(items.list.call_count, 1)
        self.assertEqual(items.list.call_args.kwargs["playlistId"], album_id)
        public.get_album.assert_called_once_with("MPREb_testalbum")

    def test_upload_and_repair_music_row_before_preparing_a_video_job(self):
        _, videos, _, items = self.mock_album_services()
        page = self.new_session()
        self.run_page(page)
        page.radio(key="workspace_view").set_value("음원 목록")
        self.run_page(page)
        store = self.music.Checkpoint(self.music_db)
        uploader = next(w for w in page.get("file_uploader") if w.label == "음원 목록 업로드")
        if hasattr(uploader, "set_value"):
            csv = "가수,곡명,앨범\n테스트 가수,이전 곡,테스트 앨범\n테스트 가수,확인할 곡,테스트 앨범\n"
            uploader.set_value(("입력.csv", csv.encode("utf-8-sig"), "text/csv"))
            self.run_page(page)
            next(b for b in page.button if b.label == "선택한 2곡으로 작업 만들기").click()
            self.run_page(page)
            job = page.selectbox(key="music_job").value
        else:
            # Earlier supported Streamlit versions cannot upload files through AppTest.
            job = store.create("입력.csv", [("테스트 가수", title, "테스트 앨범") for title in ("이전 곡", "확인할 곡")])
        old = {"videoId": "old00000001", "title": "이전 곡", "artist": "테스트 가수", "score": 100}
        store.result(job, 1, "High", [old])
        store.delivered(job, [("added", 1)])
        self.run_page(page)
        source = page.selectbox(key=f"music_lookup_row_{job}")
        self.assertEqual(len(source.options), 1)
        source.set_value(2)
        self.run_page(page)
        next(b for b in page.button if b.label == "앨범에서 이 곡 찾기").click()
        self.run_page(page)
        self.assertEqual(page.radio(key="workspace_view").value, "앨범 수록곡")
        self.assertEqual(page.text_input(key="public_album_artist").value, "테스트 가수")
        self.assertEqual(page.text_input(key="public_album_title").value, "테스트 앨범")
        self.select_second_album_track(page)
        self.assertEqual(page.radio(key="workspace_view").value, "음원 목록")
        self.assertEqual(page.selectbox(key="music_job").value, job)
        self.assertEqual([(r["video"], r["status"], r["delivery"]) for r in store.tracks(job)],
                         [("old00000001", "High", "added"), (videos[1], "Manual", "pending")])

        video_store = self.video.Store(self.video_db)
        self.assertFalse(video_store.jobs())
        for _ in range(2):
            next(b for b in page.button if b.label == "확정한 곡으로 영상 작업 만들기").click()
            self.run_page(page)
            self.assertEqual(page.radio(key="workspace_view").value, "영상 목록")
            video_job = page.selectbox(key="video_job_id").value
            self.assertEqual([(r["video"], r["registration"]) for r in video_store.items(video_job)],
                             [(videos[1], "pending")])
            page.radio(key="workspace_view").set_value("음원 목록")
            self.run_page(page)
        self.assertEqual([r["id"] for r in video_store.jobs()], [video_job])
        self.assertEqual(items.list.call_count, 1)
