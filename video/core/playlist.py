"""YouTube Data API v3 등록과 비로그인 모드의 영상 묶음 링크."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
QUOTA_MESSAGE = (
    "YouTube API의 일일 할당량에 도달했습니다(기본 10,000유닛). "
    "태평양 시간 자정(한국 시간 오후 4시 또는 5시)에 초기화됩니다. "
    "초기화 후 같은 작업에서 이어서 진행하세요."
)


class PlaylistError(Exception):
    """사용자에게 안전하게 표시할 수 있는 등록 오류."""


class Rejected(PlaylistError):
    """서버에서 명시적으로 거절했거나 요청 자체가 전송되지 않았다."""


class VideoUnavailable(Rejected):
    """해당 영상 하나를 건너뛰고 다음 항목 등록이 가능한 오류."""


class QuotaExceeded(Rejected):
    """일일 할당량 도달 시 호출을 중단하고 체크포인트를 유지한다."""


def quota_day() -> str:
    """일일 집계 경계는 미국 태평양 시간의 자정이다."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def quick_links(video_ids: Iterable[str]) -> list[str]:
    """유효한 ID를 입력 순서대로 중복 제거하고 50개씩 링크로 묶는다."""
    ids = list(dict.fromkeys(vid for vid in video_ids if VIDEO_ID.fullmatch(vid)))
    return [
        "https://www.youtube.com/watch_videos?video_ids=" + ",".join(ids[start:start + 50])
        for start in range(0, len(ids), 50)
    ]


def _api_error(error: HttpError) -> PlaylistError:
    try:
        reasons = {
            e.get("reason") for e in json.loads(error.content)["error"].get("errors", [])
            if isinstance(e, dict)
        }
    except (ValueError, KeyError, AttributeError, TypeError):
        reasons = set()
    if error.resp.status == 403 and reasons & {"quotaExceeded", "dailyLimitExceeded"}:
        return QuotaExceeded(QUOTA_MESSAGE)
    if error.resp.status == 429 or reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
        return Rejected("요청이 많습니다. 잠시 기다린 뒤 같은 작업을 이어서 진행하세요.")
    if reasons & {"youtubeSignupRequired", "channelNotFound"}:
        return Rejected("YouTube에서 본인 계정의 채널을 만든 뒤 다시 로그인하세요.")
    if reasons & {"accessNotConfigured", "serviceDisabled"}:
        return Rejected("OAuth 클라이언트의 Google Cloud 프로젝트에서 YouTube Data API v3를 활성화하세요.")
    if reasons & {"videoNotFound", "videoUnavailable", "invalidVideoId", "videoAlreadyInAnotherSeriesPlaylist"}:
        return VideoUnavailable("이 영상을 추가할 수 없습니다. 검색 후보에서 다른 영상을 선택하세요.")
    if error.resp.status in {401, 403}:
        return Rejected("로그인 계정의 YouTube 권한과 대상 재생목록의 편집 권한을 확인하세요.")
    if error.resp.status == 404:
        return Rejected("요청한 영상 또는 재생목록을 찾을 수 없습니다. 링크와 접근 권한을 확인하세요.")
    error_type = Rejected if 400 <= error.resp.status < 500 and error.resp.status != 408 else PlaylistError
    return error_type(f"YouTube 요청이 완료되지 않았습니다(HTTP {error.resp.status}). 대상 재생목록을 확인한 뒤 이어서 진행하세요.")


class Playlists:
    def __init__(self, service: Resource, charge: Callable[[int], None]) -> None:
        self.service = service
        self.charge = charge

    def _call(self, request: HttpRequest, units: int) -> dict[str, Any]:
        # 실제 할당량은 서버가 결정한다. 전송 직전 요청을 집계해 중단 시에도 추정치를 유지한다.
        self.charge(units)
        try:
            return request.execute(num_retries=0)
        except HttpError as error:
            raise _api_error(error) from None
        except Exception:
            raise PlaylistError("YouTube의 응답을 확인하지 못했습니다. 인터넷 연결과 대상 재생목록을 확인한 뒤 이어서 진행하세요.") from None

    def _items(self, resource: Resource, **params: Any) -> Iterator[dict[str, Any]]:
        while True:
            page = self._call(resource.list(**params, maxResults=50), 1)
            yield from page.get("items", [])
            if not page.get("nextPageToken"):
                return
            params["pageToken"] = page["nextPageToken"]

    def mine(self) -> dict[str, str]:
        """페이지를 모두 순회하므로 제목이 같은 재생목록도 ID로 구분할 수 있다."""
        rows = self._items(self.service.playlists(), part="snippet", mine=True,
                           fields="nextPageToken,items(id,snippet/title)")
        return {row["id"]: row["snippet"]["title"] for row in rows}

    def existing(self, playlist_id: str) -> set[str]:
        rows = self._items(self.service.playlistItems(), part="contentDetails",
                           playlistId=playlist_id, fields="nextPageToken,items(contentDetails/videoId)")
        return {row["contentDetails"]["videoId"] for row in rows if row.get("contentDetails", {}).get("videoId")}

    def create(self, title: str, description: str, privacy: str) -> str:
        if not title.strip() or privacy not in {"private", "unlisted", "public"}:
            raise Rejected("재생목록 제목과 공개 범위를 확인하세요.")
        body = {"snippet": {"title": title.strip(), "description": description},
                "status": {"privacyStatus": privacy}}
        response = self._call(self.service.playlists().insert(part="snippet,status", body=body, fields="id"), 50)
        if not response.get("id"):
            raise PlaylistError("생성한 재생목록 ID를 확인하지 못했습니다. 내 재생목록 목록을 새로고침하여 확인하세요.")
        return response["id"]

    def add(self, playlist_id: str, video_id: str) -> None:
        if not VIDEO_ID.fullmatch(video_id):
            raise VideoUnavailable("YouTube 영상 ID 형식이 잘못되었습니다. 검색 후보를 다시 선택하세요.")
        snippet = {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}
        self._call(self.service.playlistItems().insert(part="snippet", body={"snippet": snippet}, fields="id"), 50)
