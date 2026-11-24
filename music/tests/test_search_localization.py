"""ytmusicapi의 실제 지역화 파서를 통과하는 공개 음원 검색 회귀 테스트."""

import unittest
from unittest.mock import patch

from ytmusicapi import YTMusic

from core.public_search import search_songs


def song_shelf(label: str, video_id: str) -> dict:
    # 파서가 실제로 읽는 제목·가수 열과 재생 버튼을 담은 최소 응답.
    columns = [
        {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": text}]}}}
        for text in ("테스트 곡", "가수")
    ]
    play_button = {"playNavigationEndpoint": {"watchEndpoint": {"videoId": video_id}}}
    item = {
        "flexColumns": columns,
        "overlay": {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": play_button}}},
    }
    shelf = {"title": {"runs": [{"text": label}]}, "contents": [{"musicResponsiveListItemRenderer": item}]}
    return {"contents": {"sectionListRenderer": {"contents": [{"musicShelfRenderer": shelf}]}}}


class LocalizationFallbackTests(unittest.TestCase):
    def test_english_songs_shelf_recovers_song_skipped_by_korean_filter(self):
        # 두 응답의 ID를 구분해 반환값이 실제로 영어 응답에서 왔는지 확인한다.
        responses = [song_shelf("곡", "ko000000001"), song_shelf("Songs", "en000000002")]
        with patch.object(YTMusic, "_send_request", autospec=True, side_effect=responses) as transport:
            result = search_songs("가수", "테스트 곡")

        self.assertEqual(transport.call_count, 2)
        for call, language in zip(transport.call_args_list, ("ko", "en"), strict=True):
            client, endpoint, body = call.args
            self.assertEqual((client.language, endpoint, body["query"]), (language, "search", "가수 테스트 곡"))
            self.assertIn("params", body)  # 두 요청 모두 카테고리 필터를 사용한다.
        self.assertEqual([song.video_id for song in result.candidates], ["en000000002"])
        self.assertEqual(len(result.attempts), 2)
