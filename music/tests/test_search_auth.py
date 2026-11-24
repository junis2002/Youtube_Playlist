"""실계정이나 네트워크 없이 브라우저 인증과 검색 판단을 검증한다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from requests.exceptions import Timeout
from ytmusicapi.exceptions import YTMusicServerError

from core.auth import AuthError, clean_headers, setup_and_connect
from core.matcher import SearchError, search_track


class AuthenticationTests(unittest.TestCase):
    # 실제 자격증명을 사용하지 않는 Chrome 요청 헤더 예시다.
    sample = ":authority:\r\nmusic.youtube.com\r\nAuthorization:\r\nexample\r\nCookie: first=fake;\r\n second=value\r\nx-goog-authuser\r\n0"

    def test_header_formats_and_omissions(self) -> None:
        cleaned = clean_headers(self.sample + "\ncontent-length: 124")
        self.assertEqual(cleaned, "authorization: example\ncookie: first=fake; second=value\nx-goog-authuser: 0")
        with self.assertRaisesRegex(AuthError, "Cookie"):
            clean_headers("Authorization: example\nx-goog-authuser: 0")

    @patch("core.auth.YTMusic")
    def test_only_verified_files_are_installed(self, api: Mock) -> None:
        # 라이브러리의 실제 setup()으로 임시 파일을 만든 뒤 모의 계정 API를 호출한다.
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "browser.json"
            target.write_text("previous", encoding="utf-8")
            api.return_value.get_account_info.side_effect = RuntimeError("private diagnostic")
            with self.assertRaises(AuthError) as caught:
                setup_and_connect(target, self.sample)
            self.assertNotIn("private diagnostic", str(caught.exception))
            self.assertEqual(target.read_text(encoding="utf-8"), "previous")
            self.assertEqual(list(target.parent.iterdir()), [target])
            api.return_value.get_account_info.side_effect = None
            api.return_value.get_account_info.return_value = {"accountName": "테스트"}
            setup_and_connect(target, self.sample)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["x-goog-authuser"], "0")


def _track(video: str, title: str, artist: str = "가수") -> dict[str, object]:
    return {"videoId": video, "title": title, "artists": [{"name": artist}]}


class MatchTests(unittest.TestCase):
    @patch("core.matcher.time.sleep")
    def test_instrumental_priority_and_video_fallback(self, sleep: Mock) -> None:
        client = Mock()
        client.search.side_effect = [
            [_track("AAAAAAAAAAA", "노래")],
            [],
            [_track("BBBBBBBBBBB", "노래 (Instrumental)")],
        ]
        status, choices = search_track(client, "가수", "노래 (Inst.)", delay=0.2)
        self.assertEqual(status, "High")
        self.assertEqual([item["videoId"] for item in choices], ["BBBBBBBBBBB", "AAAAAAAAAAA"])
        self.assertEqual(client.search.call_args.kwargs["filter"], "videos")
        self.assertEqual(sleep.call_args_list, [call(0.2)] * 3)

    @patch("core.matcher.time.sleep")
    def test_retries_and_unfinished_network_failure(self, sleep: Mock) -> None:
        client = Mock()
        client.search.side_effect = [YTMusicServerError("Server returned HTTP 429: slow down"), [_track("AAAAAAAAAAA", "노래")]]
        status, _ = search_track(client, "가수", "노래", delay=0.2)
        self.assertEqual(status, "High")
        self.assertIn(call(1), sleep.call_args_list)
        client.search.side_effect = Timeout("timeout")
        with self.assertRaises(SearchError):
            search_track(client, "가수", "노래", delay=0)

    @patch("core.matcher.time.sleep")
    def test_review_and_empty(self, sleep: Mock) -> None:
        client = Mock()
        client.search.return_value = [_track("AAAAAAAAAAA", "노래", "다른 아티스트")]
        status, choices = search_track(client, "가수", "노래")
        self.assertEqual((status, len(choices)), ("Low", 1))
        client.search.return_value = []
        self.assertEqual(search_track(client, "가수", "노래", query="수정 쿼리"), ("Not Found", []))


if __name__ == "__main__":
    unittest.main()
