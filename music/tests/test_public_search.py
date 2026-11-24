"""계정·네트워크 없이 실제 음원 후보 판별과 조회 경로 전환을 검증한다."""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from requests.exceptions import HTTPError, Timeout

from core.public_search import PublicSearchError, export_url, search_songs


class PublicSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        constructor = patch("core.public_search.YTMusic", autospec=True)
        self.ytmusic = constructor.start()
        self.addCleanup(constructor.stop)
        self.queries: list[tuple[str, str | None, str]] = []
        self.replies: dict[tuple[str, str | None], Any] = {}
        self.ytmusic.side_effect = self.public_client

    def public_client(self, *, auth: None, language: str) -> MagicMock:
        # 언어가 바뀐 뒤에도 가수·제목과 익명 인증 조건을 그대로 검사한다.
        self.assertIsNone(auth)
        self.assertIn(language, ("ko", "en"))
        client = MagicMock()

        def search(query: str, filter: str | None = None, limit: int = 10) -> Any:
            self.queries.append((language, filter, query))
            self.assertGreater(limit, 0)
            reply = self.replies[(language, filter)]
            if isinstance(reply, Exception):
                raise reply
            return reply

        client.search.side_effect = search
        return client

    def test_korean_songs_stop_on_valid_candidates_and_export_selected_id(self) -> None:
        # 외국어 표기가 있는 다른 녹음도 반환 순서와 원본 정보를 유지한다.
        self.replies[("ko", "songs")] = [
            {"resultType": "song", "title": "사건의 지평선 (Live)", "videoId": "live0000001",
             "artists": [{"name": "윤하"}], "album": {"name": "공연"}},
            {"resultType": "song", "title": "Event Horizon (사건의 지평선)", "videoId": "j1uXcHwLhHM",
             "artists": [{"name": "YOUNHA"}, {"name": "참여 가수"}], "album": {"name": "원곡"}},
        ]
        result = search_songs("윤하", "사건의 지평선")
        self.ytmusic.assert_called_once_with(auth=None, language="ko")
        self.assertEqual(self.queries, [("ko", "songs", "윤하 사건의 지평선")])
        self.assertEqual(len(result.attempts), 1)
        self.assertIn("2", result.attempts[0])
        self.assertEqual([c.video_id for c in result.candidates], ["live0000001", "j1uXcHwLhHM"])
        selected = result.candidates[1]
        self.assertEqual(selected.title, "Event Horizon (사건의 지평선)")
        self.assertEqual(selected.artists, ("YOUNHA", "참여 가수"))
        self.assertEqual(selected.album, "원곡")
        self.assertEqual(export_url(selected), b"https://www.youtube.com/watch?v=j1uXcHwLhHM\n")

    def test_english_songs_retry_with_original_query_for_another_artist(self) -> None:
        self.replies[("ko", "songs")] = []
        self.replies[("en", "songs")] = [
            {"resultType": "song", "title": "밤편지", "videoId": "song0000002",
             "artists": [{"name": "아이유"}]}
        ]
        result = search_songs("아이유", "밤편지")
        self.assertEqual(self.queries, [("ko", "songs", "아이유 밤편지"), ("en", "songs", "아이유 밤편지")])
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("한국어 곡 검색", result.attempts[0])
        self.assertIn("영어 곡 검색", result.attempts[1])
        self.assertIn("0", result.attempts[0])
        self.assertIn("1", result.attempts[1])
        self.assertEqual([c.video_id for c in result.candidates], ["song0000002"])

    def test_mixed_search_finds_only_explicit_song_after_many_other_types(self) -> None:
        self.replies[("ko", "songs")] = [{"resultType": "song", "title": "ID 없는 곡", "browseId": "album000001"}]
        self.replies[("en", "songs")] = []
        # 일반 뮤직비디오도 실제 videoId를 갖지만 음원 후보로 넘기면 안 된다.
        non_songs = [{"resultType": kind, "title": "같은 제목", "videoId": f"{i:011d}"}
                     for i, kind in enumerate(["video", "artist", "album", "playlist"] * 3)]
        self.replies[("en", None)] = [
            *non_songs,
            {"title": "유형 미확인", "videoId": "notasong001"},
            {"resultType": "song", "title": "원곡", "videoId": "song0000002"},
        ]
        result = search_songs("윤하", "사건의 지평선")
        self.assertEqual(self.queries, [(language, kind, "윤하 사건의 지평선")
                                        for language, kind in [("ko", "songs"), ("en", "songs"), ("en", None)]])
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual([c.video_id for c in result.candidates if c.video_id], ["song0000002"])

    def test_exhausted_routes_keep_real_songs_with_unusable_ids_for_inspection(self) -> None:
        bad_fields = [
            {"browseId": "album000001", "album": {"id": "album000002", "name": "앨범"}},
            {"videoId": "too-short"},
            {"videoId": "song0000002&list=extra"},
        ]
        self.replies[("ko", "songs")] = [
            {"resultType": "song", "title": f"후보 {i}", **fields} for i, fields in enumerate(bad_fields)
        ]
        self.replies[("en", "songs")] = []
        self.replies[("en", None)] = [{"resultType": "artist", "title": "가수", "browseId": "artist00001"}]
        result = search_songs("윤하", "사건의 지평선")
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual([c.title for c in result.candidates], ["후보 0", "후보 1", "후보 2"])
        for candidate in result.candidates:
            self.assertIsNone(candidate.video_id)
            with self.assertRaises(ValueError):
                export_url(candidate)

    def test_no_results_after_all_routes_is_distinct_from_service_failure(self) -> None:
        self.replies.update({("ko", "songs"): [], ("en", "songs"): [],
                             ("en", None): [{"resultType": "video", "videoId": "live0000001"}]})
        result = search_songs("윤하", "사건의 지평선")
        self.assertEqual(result.candidates, [])
        self.assertEqual(len(result.attempts), 3)
        for label, attempt in zip(("한국어 곡 검색", "영어 곡 검색", "영어 통합 검색"), result.attempts):
            self.assertIn(label, attempt)

    def test_mixed_rows_keep_diagnostics_and_only_available_audio_is_selectable(self) -> None:
        mixed = [
            {"resultType": "video", "videoType": "MUSIC_VIDEO_TYPE_ATV", "videoId": "track000001", "title": "원곡"},
            {"resultType": "video", "videoType": "MUSIC_VIDEO_TYPE_OMV", "videoId": "video000001"},
            {"resultType": "song", "videoType": "MUSIC_VIDEO_TYPE_UGC", "videoId": "cover000001"},
            {"resultType": "album", "videoType": "MUSIC_VIDEO_TYPE_ATV", "videoId": "album000001"},
            {"resultType": "song", "videoId": "block000001", "isAvailable": False},
            {"resultType": "artist", "artist": "가수"},
        ]
        self.replies.update({("ko", "songs"): [], ("en", "songs"): [], ("en", None): mixed})
        result = search_songs("가수", "원곡")
        self.assertEqual([song.video_id for song in result.candidates], ["track000001"])
        self.assertEqual([row.video_id for row in result.mixed_rows], [row.get("videoId") for row in mixed])
        self.assertEqual(result.mixed_rows[1].video_type, "MUSIC_VIDEO_TYPE_OMV")
        self.assertEqual((result.mixed_rows[-1].result_type, result.mixed_rows[-1].title), ("artist", "가수"))

    def test_timeout_or_http_failure_after_empty_response_is_reported_without_raw_details(self) -> None:
        for error in (Timeout("PRIVATE-DETAIL"), HTTPError("PRIVATE-DETAIL")):
            with self.subTest(error=type(error).__name__):
                self.queries.clear()
                self.replies.update({("ko", "songs"): [], ("en", "songs"): [],
                                     ("en", None): error})
                with self.assertRaises(PublicSearchError) as caught:
                    search_songs("윤하", "사건의 지평선")
                self.assertEqual(len(self.queries), 3)
                diagnostic = str(caught.exception)
                for label in ("한국어 곡 검색", "영어 곡 검색", "영어 통합 검색"):
                    self.assertIn(label, diagnostic)
                self.assertNotIn("PRIVATE-DETAIL", diagnostic)

    def test_incomplete_query_does_not_open_a_client(self) -> None:
        for artist, title in [(" ", "사건의 지평선"), ("윤하", "")]:
            with self.subTest(artist=artist, title=title), self.assertRaises(ValueError):
                search_songs(artist, title)
        self.ytmusic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
