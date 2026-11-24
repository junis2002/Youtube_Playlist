"""공식 YouTube 클라이언트를 모의 전송기에 연결해 앨범 조회 전용 경로를 검증한다."""

import json
import unittest
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httplib2
from googleapiclient.discovery import build

from core.playlist import AlbumTrack, AlbumTracks, PlaylistError, Playlists, QuotaExceeded

LIST_ID = "OLAK5uy_Test-123_456"
AUDIO_1 = "v0000000001"
AUDIO_2 = "v0000000002"


class Transport:
    def __init__(self, *responses: tuple[int, dict[str, Any]] | Exception) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str]] = []

    def request(self, uri: str, method: str = "GET", **kwargs: Any) -> tuple[httplib2.Response, bytes]:
        self.calls.append((method, uri))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        code, payload = value
        return httplib2.Response({"status": str(code), "content-type": "application/json"}), json.dumps(payload).encode()


def client(transport: Transport, charged: list[int]) -> Playlists:
    # 로컬 discovery 문서를 사용하며 모든 HTTP 요청은 위의 모의 전송기에만 도달한다.
    api = build("youtube", "v3", http=transport, static_discovery=True, cache_discovery=False)
    return Playlists(api, charged.append)


class AlbumReadTests(unittest.TestCase):
    def test_pagination_order_real_video_ids_and_read_only_requests(self) -> None:
        items1 = [
            {"id": "item-id", "snippet": {"title": "트랙 하나", "position": 0,
                                           "videoOwnerChannelTitle": "가수 채널",
                                           "channelTitle": "목록 채널",
                                           "resourceId": {"kind": "youtube#video", "videoId": AUDIO_1}},
             "contentDetails": {"videoId": AUDIO_1}},
            {"id": AUDIO_2, "snippet": {"title": "재생목록 항목 ID만 있음"}},
            {"snippet": {"title": "충돌", "resourceId": {"kind": "youtube#video", "videoId": AUDIO_2}},
             "contentDetails": {"videoId": AUDIO_1}},
            {"snippet": {"title": "유형 불명", "resourceId": {"videoId": AUDIO_1}}},
            {"snippet": {"title": "영상이 아님", "resourceId": {"kind": "youtube#channel", "videoId": AUDIO_1}}},
            {"snippet": {"title": "ID 오류"}, "contentDetails": {"videoId": "invalid"}},
            {"snippet": {"title": "   "}, "contentDetails": {"videoId": AUDIO_1}},
            {"snippet": {"title": "[Deleted video]"}, "contentDetails": {"videoId": AUDIO_1}},
            {"snippet": {"title": "Private video"}, "contentDetails": {"videoId": AUDIO_1}},
            {"snippet": {"title": "공개 전"}, "contentDetails": {"videoId": AUDIO_1},
             "status": {"privacyStatus": "private"}},
        ]
        items2 = [
            {"snippet": {"title": "트랙 둘", "position": 10, "videoOwnerChannelTitle": "가수",
                         "resourceId": {"kind": "youtube#video", "videoId": AUDIO_2}}},
            {"snippet": {"title": "다시 담긴 트랙", "channelTitle": "목록 채널"}, "contentDetails": {"videoId": AUDIO_1}},
        ]
        transport = Transport((200, {"items": items1, "nextPageToken": "second"}), (200, {"items": items2}))
        charged: list[int] = []
        result = client(transport, charged).album_tracks(LIST_ID)
        self.assertEqual(result, AlbumTracks(len(items1) + len(items2), [
            AlbumTrack(AUDIO_1, "트랙 하나", "가수 채널", 0),
            AlbumTrack(AUDIO_2, "트랙 둘", "가수", 10),
            AlbumTrack(AUDIO_1, "다시 담긴 트랙", "", None),
        ]))
        self.assertEqual(charged, [1, 1])
        for i, (method, url) in enumerate(transport.calls):
            self.assertEqual(method, "GET")
            self.assertTrue(urlsplit(url).path.endswith("/playlistItems"))
            params = parse_qs(urlsplit(url).query)
            self.assertEqual(params["playlistId"], [LIST_ID])
            self.assertEqual(params["maxResults"], ["50"])
            self.assertEqual(params.get("pageToken"), ["second"] if i else None)

    def test_invalid_albums_cause_no_request_or_charge(self) -> None:
        transport = Transport()
        charged: list[int] = []
        api = client(transport, charged)
        for wrong in ("OLAK5uy_", "PL-mine", LIST_ID + "\n", "x" + LIST_ID, "OLAK5uy_a?query=x",
                      "https://www.youtube.com/playlist?list=" + LIST_ID):
            with self.subTest(wrong=wrong), self.assertRaises(PlaylistError):
                api.album_tracks(wrong)
        self.assertEqual(charged, [])
        self.assertEqual(transport.calls, [])

    def test_empty_album_and_omitted_empty_items(self) -> None:
        for payload in ({"items": []}, {}):
            with self.subTest(payload=payload):
                self.assertEqual(client(Transport((200, payload)), []).album_tracks(LIST_ID), AlbumTracks(0, []))

    def test_http_and_transport_errors_stop_without_partial_result(self) -> None:
        # 사용자에게 전달되는 오류에는 원본 응답 본문이나 대상 URL이 포함되지 않는다.
        for code, reason, error_type in [(401, "unauthorized", PlaylistError), (403, "forbidden", PlaylistError),
                                         (404, "playlistNotFound", PlaylistError), (403, "quotaExceeded", QuotaExceeded)]:
            payload = {"error": {"errors": [{"reason": reason}], "message": "internal-details"}}
            transport = Transport((200, {"items": [], "nextPageToken": "next"}), (code, payload))
            charged: list[int] = []
            with self.subTest(code=code, reason=reason), self.assertRaises(error_type) as cm:
                client(transport, charged).album_tracks(LIST_ID)
            for unwanted in ("internal-details", "편집 권한", "추가할 수"):
                self.assertNotIn(unwanted, str(cm.exception))
            self.assertEqual(charged, [1, 1])
            self.assertTrue(all(method == "GET" for method, _ in transport.calls))
        transport = Transport(ConnectionError("internal-details"))
        with self.assertRaisesRegex(PlaylistError, "연결") as cm:
            client(transport, []).album_tracks(LIST_ID)
        self.assertNotIn("internal-details", str(cm.exception))

    def test_repeated_pagination_token_does_not_waste_quota(self) -> None:
        page = {"items": [], "nextPageToken": "again"}
        transport = Transport((200, page), (200, page))
        charged: list[int] = []
        with self.assertRaisesRegex(PlaylistError, "다음 페이지"):
            client(transport, charged).album_tracks(LIST_ID)
        self.assertEqual(charged, [1, 1])
