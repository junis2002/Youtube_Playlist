"""등록 미리보기가 조회 호출만 사용하고 없는 계정·목록 정보를 구분하는지 검증한다."""

import json
import unittest
from unittest.mock import Mock, call

import httplib2
from googleapiclient.errors import HttpError

from core.playlist import PlaylistError, PlaylistInfo, Playlists, QuotaExceeded


class PreviewMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = Mock(spec_set=["channels", "playlists"])
        # 쓰기 메서드에 접근하면 모의 리소스에서 바로 실패한다.
        self.channels = Mock(spec_set=["list"])
        self.lists = Mock(spec_set=["list"])
        self.service.channels.return_value = self.channels
        self.service.playlists.return_value = self.lists
        self.units: list[int] = []
        self.api = Playlists(self.service, self.units.append)

    def test_one_connected_channel_or_unknown(self) -> None:
        rows = [{"snippet": {"title": "내 채널"}}]
        for page, expected in [({"items": rows}, "내 채널"), ({"items": []}, ""),
                               ({"items": rows * 2}, ""), ({"items": rows, "nextPageToken": "more"}, "")]:
            with self.subTest(page=page):
                self.channels.list.return_value.execute.return_value = page
                self.assertEqual(self.api.channel_title(), expected)
        self.assertEqual(self.units, [1] * 4)
        self.assertTrue(self.channels.list.call_args.kwargs["mine"])
        self.service.playlists.assert_not_called()

    def test_playlist_lookup_only_returns_matching_id(self) -> None:
        self.lists.list.return_value.execute.side_effect = [
            {"items": [{"id": "PL-A", "snippet": {"title": "이름"}, "status": {"privacyStatus": "private"}}]},
            {"items": [{"id": "PL-B", "snippet": {"title": "다른 목록"}}]},
        ]
        self.assertEqual(self.api.playlist_info("PL-A"), PlaylistInfo("PL-A", "이름", "private"))
        self.assertIsNone(self.api.playlist_info("PL-A"))
        self.assertEqual(self.lists.list.call_args.kwargs["id"], "PL-A")
        self.assertNotIn("mine", self.lists.list.call_args.kwargs)
        self.assertEqual(self.units, [1, 1])

    def test_mine_includes_each_page_and_preserves_unknown_privacy(self) -> None:
        self.lists.list.return_value.execute.side_effect = [
            {"items": [{"id": "PL-A", "snippet": {"title": "같은 제목"}}], "nextPageToken": "next"},
            {"items": [{"id": "PL-B", "snippet": {"title": "같은 제목"}, "status": {"privacyStatus": "public"}}]},
        ]
        self.assertEqual(self.api.mine_info(), {
            "PL-A": PlaylistInfo("PL-A", "같은 제목", ""),
            "PL-B": PlaylistInfo("PL-B", "같은 제목", "public"),
        })
        self.assertEqual(self.lists.list.call_args.kwargs["pageToken"], "next")
        self.assertTrue(self.lists.list.call_args.kwargs["mine"])
        self.assertEqual(self.units, [1, 1])

    def test_read_errors_charge_and_keep_server_details_private(self) -> None:
        for code, reason, cls in [(403, "quotaExceeded", QuotaExceeded), (403, "forbidden", PlaylistError)]:
            with self.subTest(code=code, reason=reason):
                response = {"error": {"errors": [{"reason": reason}], "message": "sensitive-server-text"}}
                self.channels.list.return_value.execute.side_effect = HttpError(
                    httplib2.Response({"status": str(code)}), json.dumps(response).encode()
                )
                with self.assertRaises(cls) as caught:
                    self.api.channel_title()
                self.assertNotIn("sensitive-server-text", str(caught.exception))
        self.assertEqual(self.channels.list.return_value.execute.call_args_list, [call(num_retries=0)] * 2)
        self.assertEqual(self.units, [1, 1])
