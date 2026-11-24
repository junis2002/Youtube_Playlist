"""공개 앨범 조회에서 명시적으로 고른 앨범과 재생 가능한 트랙만 전달하는지 검사한다."""

import unittest
from unittest.mock import call, patch

from requests.exceptions import Timeout

from core.public_album import AlbumOption, find_albums, load_album_tracks
from core.public_search import PublicSearchError, export_url


class PublicAlbumTests(unittest.TestCase):
    def setUp(self) -> None:
        constructor = patch("core.public_album.YTMusic", autospec=True)
        self.ytmusic = constructor.start()
        self.addCleanup(constructor.stop)
        self.client = self.ytmusic.return_value
        self.option = AlbumOption(title="밤의 기록", artists=("가수",), browse_id="MPREb_album-01")

    def test_album_search_preserves_releases_without_opening_them(self) -> None:
        first = {"resultType": "album", "title": "밤의 기록", "browseId": "MPREb_album-01",
                 "artists": [{"name": "가수"}]}
        second = {"resultType": "album", "title": "밤의 기록 (Live)", "browseId": "MPREb_album-02",
                  "artists": [{"name": "가수"}, {"name": "게스트"}]}
        self.client.search.return_value = [first, first, second]
        albums = find_albums("가수", "밤의 기록")
        self.assertEqual(albums, [self.option, AlbumOption(title="밤의 기록 (Live)", artists=("가수", "게스트"),
                                                         browse_id="MPREb_album-02")])
        self.ytmusic.assert_called_once_with(auth=None, language="en")
        self.client.search.assert_called_once_with("가수 밤의 기록", filter="albums", limit=10)
        self.client.get_album.assert_not_called()

    def test_mixed_fallback_accepts_only_real_albums_with_their_own_browse_id(self) -> None:
        invalid = [
            {"resultType": "album", "title": "목록 ID", "playlistId": "MPREb_album-05"},
            {"resultType": "album", "title": "목록", "browseId": "VLplaylist0001"},
            {"resultType": "album", "title": "손상된 ID", "browseId": "MPREbad&list=extra"},
            {"resultType": "album", "title": "", "browseId": "MPREb_empty"},
        ]
        mixed = [
            {"resultType": "artist", "title": "가수", "browseId": "MPREb_artist-01"},
            {"title": "유형 미확인", "browseId": "MPREb_unknown"},
            *invalid,
            {"resultType": "album", "title": self.option.title, "artist": "가수",
             "browseId": self.option.browse_id},
        ]
        self.client.search.side_effect = [invalid, mixed]
        self.assertEqual(find_albums("가수", "밤의 기록"), [self.option])
        self.assertEqual(self.client.search.call_args_list, [
            call("가수 밤의 기록", filter="albums", limit=10), call("가수 밤의 기록", filter=None, limit=10),
        ])
        self.client.get_album.assert_not_called()

    def test_only_playable_audio_tracks_of_selected_album_can_be_exported(self) -> None:
        # 같은 곡의 버전은 제목이나 가수로 합치지 않고 실제 트랙 ID를 유지한다.
        tracks = [
            {"title": "원곡", "videoId": "albumtrk001", "videoType": "MUSIC_VIDEO_TYPE_ATV",
             "artists": [{"name": "참여 가수"}]},
            {"title": "원곡 (반주)", "videoId": "albumtrk002", "artists": []},
            {"title": "미공개", "videoId": "albumtrk003", "isAvailable": False},
            {"title": "뮤직비디오", "videoId": "albumtrk004", "videoType": "MUSIC_VIDEO_TYPE_OMV"},
            {"title": "팬 영상", "videoId": "albumtrk005", "videoType": "MUSIC_VIDEO_TYPE_UGC"},
            {"title": "유형 확인 불가", "videoId": "albumtrk008", "videoType": "MUSIC_VIDEO_TYPE_UNKNOWN"},
            {"title": "ID 없음", "browseId": "albumtrk006"},
            {"title": "ID 손상", "videoId": "albumtrk007&list=extra"},
        ]
        self.client.get_album.return_value = {"title": "밤의 기록 (확장판)", "artists": [{"name": "앨범 가수"}],
                                              "tracks": tracks}
        candidates = load_album_tracks(self.option)
        self.ytmusic.assert_called_once_with(auth=None, language="en")
        self.client.get_album.assert_called_once_with(self.option.browse_id)
        self.client.search.assert_not_called()
        self.assertEqual([(c.title, c.artists, c.album, c.video_id) for c in candidates], [
            ("원곡", ("참여 가수",), "밤의 기록 (확장판)", "albumtrk001"),
            ("원곡 (반주)", ("앨범 가수",), "밤의 기록 (확장판)", "albumtrk002"),
        ])
        self.assertEqual(export_url(candidates[1]), b"https://www.youtube.com/watch?v=albumtrk002\n")

    def test_empty_and_invalid_queries(self) -> None:
        for artist, title in [("", "앨범"), ("가수", " ")]:
            with self.subTest(artist=artist, title=title), self.assertRaises(ValueError):
                find_albums(artist, title)
        self.ytmusic.assert_not_called()
        self.client.search.side_effect = [[], []]
        self.assertEqual(find_albums("가수", "앨범"), [])
        self.client.get_album.return_value = {"title": "밤의 기록", "tracks": []}
        self.assertEqual(load_album_tracks(self.option), [])

    def test_request_and_format_errors_are_reported_without_raw_details(self) -> None:
        self.client.search.side_effect = Timeout("PRIVATE-DETAIL")
        with self.assertRaises(PublicSearchError) as caught:
            find_albums("가수", "앨범")
        self.assertNotIn("PRIVATE-DETAIL", str(caught.exception))
        self.client.search.side_effect = None
        self.client.search.return_value = {"error": "PRIVATE-DETAIL"}
        with self.assertRaises(PublicSearchError) as caught:
            find_albums("가수", "앨범")
        self.assertNotIn("PRIVATE-DETAIL", str(caught.exception))
        self.client.get_album.side_effect = [Timeout("PRIVATE-DETAIL"), {"tracks": "PRIVATE-DETAIL"}]
        for case in ("연결 실패", "트랙 형식 오류"):
            with self.subTest(case=case):
                with self.assertRaises(PublicSearchError) as caught:
                    load_album_tracks(self.option)
                self.assertNotIn("PRIVATE-DETAIL", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
