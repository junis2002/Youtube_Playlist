"""YouTube Data API v3 등록과 비로그인 모드의 영상 묶음 링크."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

ALBUM_PLAYLIST_ID = re.compile(r"OLAK5uy_[A-Za-z0-9_-]+")
UNAVAILABLE_TITLES = {"private video", "deleted video", "비공개 동영상", "삭제된 동영상", "비공개 영상", "삭제된 영상"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
QUOTA_MESSAGE = (
    "YouTube API의 일일 할당량에 도달했습니다(기본 10,000유닛). "
    "태평양 시간 자정(한국 시간 오후 4시 또는 5시)에 초기화됩니다. "
    "초기화 후 같은 작업에서 이어서 진행하세요."
)


@dataclass(frozen=True)
class PlaylistInfo:
    id: str
    title: str
    privacy: str


@dataclass(frozen=True)
class AlbumTrack:
    video_id: str
    title: str
    channel: str
    position: int | None  # API 원본은 0부터 시작한다.


@dataclass(frozen=True)
class AlbumTracks:
    raw_count: int
    entries: list[AlbumTrack]


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


def _api_error(error: HttpError, *, read_subject: str = "") -> PlaylistError:
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
    if not read_subject and reasons & {"youtubeSignupRequired", "channelNotFound"}:
        return Rejected("YouTube에서 본인 계정의 채널을 만든 뒤 다시 로그인하세요.")
    if reasons & {"accessNotConfigured", "serviceDisabled"}:
        return Rejected("OAuth 클라이언트의 Google Cloud 프로젝트에서 YouTube Data API v3를 활성화하세요.")
    if read_subject:
        if error.resp.status == 401:
            return Rejected("계정 인증을 확인할 수 없습니다. 다시 로그인한 뒤 조회하세요.")
        if error.resp.status == 403:
            return Rejected(f"YouTube가 {read_subject} 조회를 허용하지 않았습니다. 계정과 접근 권한을 확인하세요.")
        if error.resp.status == 404:
            return Rejected(f"공식 API에서 {read_subject} 정보를 찾지 못했습니다. 계정과 접근 권한을 확인하세요.")
        return PlaylistError(f"{read_subject} 조회를 완료하지 못했습니다(HTTP {error.resp.status}). 잠시 후 다시 조회하세요.")
    if reasons & {"videoNotFound", "videoUnavailable", "invalidVideoId", "videoAlreadyInAnotherSeriesPlaylist"}:
        return VideoUnavailable("이 영상을 추가할 수 없습니다. 검색 후보에서 다른 영상을 선택하세요.")
    if error.resp.status in {401, 403}:
        return Rejected("로그인 계정의 YouTube 권한과 대상 재생목록의 편집 권한을 확인하세요.")
    if error.resp.status == 404:
        return Rejected("요청한 영상 또는 재생목록을 찾을 수 없습니다. 링크와 접근 권한을 확인하세요.")
    error_type = Rejected if 400 <= error.resp.status < 500 and error.resp.status != 408 else PlaylistError
    return error_type(f"YouTube 요청이 완료되지 않았습니다(HTTP {error.resp.status}). 대상 재생목록을 확인한 뒤 이어서 진행하세요.")


def _album_track(row: object) -> AlbumTrack | None:
    if not isinstance(row, dict) or not isinstance(snippet := row.get("snippet"), dict):
        return None
    title = snippet.get("title")
    if not isinstance(title, str):
        return None
    title = title.strip()
    if not title or title.strip("[] ").casefold() in UNAVAILABLE_TITLES:
        return None
    status = row.get("status")
    if isinstance(status, dict) and status.get("privacyStatus") == "private":
        return None
    details, resource = row.get("contentDetails"), snippet.get("resourceId")
    detail_id = details.get("videoId") if isinstance(details, dict) else None
    resource_id = resource.get("videoId") if isinstance(resource, dict) else None
    kind = resource.get("kind") if isinstance(resource, dict) else None
    if kind not in (None, "youtube#video"):
        return None
    if detail_id is not None and resource_id is not None and detail_id != resource_id:
        return None
    # playlistItem 자체의 id와 목록 소유 채널은 트랙 정보로 사용하지 않는다.
    vid = detail_id
    if vid is None and kind == "youtube#video":
        vid = resource_id
    if not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid):
        return None
    channel = snippet.get("videoOwnerChannelTitle")
    position = snippet.get("position")
    return AlbumTrack(vid, title, channel.strip() if isinstance(channel, str) else "",
                      position if type(position) is int and position >= 0 else None)


class Playlists:
    def __init__(self, service: Resource, charge: Callable[[int], None]) -> None:
        self.service = service
        self.charge = charge

    def _call(self, request: HttpRequest, units: int, *, read_subject: str = "") -> dict[str, Any]:
        # 실제 할당량은 서버가 결정한다. 전송 직전 요청을 집계해 중단 시에도 추정치를 유지한다.
        self.charge(units)
        try:
            return request.execute(num_retries=0)
        except HttpError as error:
            raise _api_error(error, read_subject=read_subject) from None
        except Exception:
            if read_subject:
                raise PlaylistError(f"{read_subject} 응답을 받지 못했습니다. 연결을 확인하고 다시 조회하세요.") from None
            raise PlaylistError("YouTube의 응답을 확인하지 못했습니다. 인터넷 연결과 대상 재생목록을 확인한 뒤 이어서 진행하세요.") from None

    def _items(self, resource: Resource, *, read_subject: str = "", **params: Any) -> Iterator[dict[str, Any]]:
        seen_tokens: set[str] = set()
        while True:
            page = self._call(resource.list(**params, maxResults=50), 1, read_subject=read_subject)
            if not isinstance(page, dict) or not isinstance(page.get("items", []), list):
                raise PlaylistError("목록 응답을 읽을 수 없습니다. 잠시 후 다시 조회하세요.")
            yield from page.get("items", [])
            token = page.get("nextPageToken")
            if not token:
                return
            if not isinstance(token, str) or token in seen_tokens:
                raise PlaylistError("목록의 다음 페이지를 확인할 수 없습니다. 잠시 후 다시 조회하세요.")
            seen_tokens.add(token)
            params["pageToken"] = token

    def channel_title(self) -> str:
        """현재 인증으로 확인되는 채널이 하나일 때 그 이름을 반환한다."""
        page = self._call(self.service.channels().list(
            part="snippet", mine=True, maxResults=2, fields="nextPageToken,items(snippet/title)",
        ), 1, read_subject="채널")
        rows = page.get("items", []) if isinstance(page, dict) else []
        if not isinstance(rows, list) or len(rows) != 1 or page.get("nextPageToken"):
            return ""
        snippet = rows[0].get("snippet") if isinstance(rows[0], dict) else None
        title = snippet.get("title") if isinstance(snippet, dict) else None
        return title.strip() if isinstance(title, str) else ""

    def _playlist_infos(self, **params: Any) -> Iterator[PlaylistInfo]:
        rows = self._items(self.service.playlists(), read_subject="재생목록", part="snippet,status",
                           fields="nextPageToken,items(id,snippet/title,status/privacyStatus)", **params)
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                continue
            snippet, status = row.get("snippet"), row.get("status")
            title = snippet.get("title") if isinstance(snippet, dict) else None
            privacy = status.get("privacyStatus") if isinstance(status, dict) else None
            # API 응답에 없는 제목·공개 범위를 추측하면 다른 목록으로 오인할 수 있다.
            yield PlaylistInfo(row["id"], title.strip() if isinstance(title, str) else "",
                               privacy if privacy in ("private", "unlisted", "public") else "")

    def mine_info(self) -> dict[str, PlaylistInfo]:
        """자신의 목록을 모두 조회하고 선택 ID별 제목과 공개 범위를 제공한다."""
        return {info.id: info for info in self._playlist_infos(mine=True)}

    def playlist_info(self, playlist_id: str) -> PlaylistInfo | None:
        """체크포인트에 저장된 대상 목록의 현재 메타데이터를 조회한다."""
        return next((info for info in self._playlist_infos(id=playlist_id) if info.id == playlist_id), None)

    def mine(self) -> dict[str, str]:
        """페이지를 모두 순회하므로 제목이 같은 재생목록도 ID로 구분할 수 있다."""
        rows = self._items(self.service.playlists(), part="snippet", mine=True,
                           fields="nextPageToken,items(id,snippet/title)")
        return {row["id"]: row["snippet"]["title"] for row in rows}

    def existing(self, playlist_id: str) -> set[str]:
        rows = self._items(self.service.playlistItems(), part="contentDetails",
                           playlistId=playlist_id, fields="nextPageToken,items(contentDetails/videoId)")
        return {row["contentDetails"]["videoId"] for row in rows if row.get("contentDetails", {}).get("videoId")}

    def album_tracks(self, playlist_id: str) -> AlbumTracks:
        """공식 앨범 목록만 읽어 전체 행 수와 내보낼 수 있는 트랙을 반환한다."""
        if not isinstance(playlist_id, str) or not ALBUM_PLAYLIST_ID.fullmatch(playlist_id):
            raise PlaylistError("OLAK5uy_로 시작하는 공식 앨범 목록 ID를 입력하세요.")
        rows = self._items(
            self.service.playlistItems(), read_subject="앨범 목록", playlistId=playlist_id, part="snippet,contentDetails,status",
            fields=("nextPageToken,items(contentDetails/videoId,status/privacyStatus,"
                    "snippet(title,position,videoOwnerChannelTitle,resourceId(kind,videoId)))"),
        )
        count = 0
        entries: list[AlbumTrack] = []
        for count, row in enumerate(rows, 1):
            if track := _album_track(row):
                entries.append(track)
        return AlbumTracks(count, entries)

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
