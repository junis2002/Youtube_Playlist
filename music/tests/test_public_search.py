"""계정·네트워크 없이 공개 음원 검색과 선택한 URL 내보내기를 검증한다."""

import unittest
from unittest.mock import patch

from requests.exceptions import Timeout

from core.public_search import PublicSearchError, export_url, search_songs


class PublicSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        constructor = patch("core.public_search.YTMusic", autospec=True)
        self.ytmusic = constructor.start()
        self.addCleanup(constructor.stop)
        self.search = self.ytmusic.return_value.search

    def test_public_client_preserves_candidates_and_exports_the_selected_id(self) -> None:
        # 같은 곡의 다른 녹음도 별도 후보로 유지해야 직접 선택할 수 있다.
        self.search.return_value = [
            {"resultType": "song", "title": "사건의 지평선 (Live)", "videoId": "live0000001",
             "artists": [{"name": "윤하"}], "album": {"name": "공연"}},
            {"resultType": "song", "title": "사건의 지평선", "videoId": "song0000002",
             "artists": [{"name": "윤하"}, {"name": "참여 가수"}], "album": {"name": "원곡"}},
        ]
        candidates = search_songs("윤하", "사건의 지평선")
        self.ytmusic.assert_called_once_with(auth=None, language="ko")
        self.search.assert_called_once_with("윤하 사건의 지평선", filter="songs", limit=10)
        self.assertEqual([c.video_id for c in candidates], ["live0000001", "song0000002"])
        selected = candidates[1]
        self.assertEqual(selected.title, "사건의 지평선")
        self.assertEqual(selected.artists, ("윤하", "참여 가수"))
        self.assertEqual(selected.album, "원곡")
        self.assertEqual(export_url(selected), b"https://www.youtube.com/watch?v=song0000002\n")

    def test_missing_or_invalid_id_never_becomes_an_exportable_url(self) -> None:
        for fields in [
            {"browseId": "album000001", "album": {"id": "album000002", "name": "앨범"}},
            {"videoId": "too-short"},
            {"videoId": "song0000002&list=extra"},
        ]:
            with self.subTest(fields=fields):
                self.search.return_value = [
                    {"resultType": "song", "title": "사건의 지평선", "artists": [{"name": "윤하"}], **fields}
                ]
                candidate, = search_songs("윤하", "사건의 지평선")
                self.assertIsNone(candidate.video_id)
                with self.assertRaises(ValueError):
                    export_url(candidate)

    def test_no_results_and_request_errors_are_distinct(self) -> None:
        self.search.return_value = []
        self.assertEqual(search_songs("윤하", "사건의 지평선"), [])
        self.search.side_effect = Timeout("transport diagnostic")
        with self.assertRaises(PublicSearchError):
            search_songs("윤하", "사건의 지평선")

    def test_incomplete_query_does_not_open_a_client(self) -> None:
        for artist, title in [(" ", "사건의 지평선"), ("윤하", "")]:
            with self.subTest(artist=artist, title=title), self.assertRaises(ValueError):
                search_songs(artist, title)
        self.ytmusic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
