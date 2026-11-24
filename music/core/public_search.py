"""공개 카탈로그에서 음원을 조회하고 선택한 후보 한 건을 URL로 내보낸다."""

import re
from dataclasses import dataclass

from ytmusicapi import YTMusic

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


class PublicSearchError(RuntimeError):
    """공개 검색 서비스를 이용할 수 없거나 응답을 읽을 수 없는 경우."""


@dataclass(frozen=True)
class SongCandidate:
    title: str
    artists: tuple[str, ...]
    album: str
    video_id: str | None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def search_songs(artist: str, title: str) -> list[SongCandidate]:
    """서비스가 반환한 순서와 메타데이터를 보존한다. 빈 목록은 정상 응답이다."""
    artist, title = artist.strip(), title.strip()
    if not artist or not title:
        raise ValueError("가수와 곡명을 모두 입력해 주세요.")
    try:
        # 개인 계정의 라이브러리와 분리된 공개 검색 클라이언트만 사용한다.
        client = YTMusic(auth=None, language="ko")
        rows = client.search(f"{artist} {title}", filter="songs", limit=10)
    except Exception:
        raise PublicSearchError("YouTube Music에 검색을 요청하지 못했습니다. 인터넷 연결을 확인하고 다시 검색해 주세요.") from None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PublicSearchError("YouTube Music의 검색 응답을 읽을 수 없습니다. 잠시 후 다시 검색해 주세요.")

    candidates: list[SongCandidate] = []
    for row in rows[:10]:
        names = row.get("artists")
        artists = tuple(
            name for item in (names if isinstance(names, list) else [])
            if isinstance(item, dict) and (name := _text(item.get("name")))
        )
        album = row.get("album")
        album_name = _text(album.get("name")) if isinstance(album, dict) else _text(album)
        raw_id = _text(row.get("videoId"))
        candidates.append(SongCandidate(
            title=_text(row.get("title")), artists=artists, album=album_name,
            video_id=raw_id if _VIDEO_ID.fullmatch(raw_id) else None,
        ))
    return candidates


def export_url(candidate: SongCandidate) -> bytes:
    """영상 앱 TXT 파서가 읽는 한 줄짜리 일반 YouTube URL을 만든다."""
    if not candidate.video_id or not _VIDEO_ID.fullmatch(candidate.video_id):
        raise ValueError("유효한 영상 ID가 있는 후보를 선택해 주세요.")
    return f"https://www.youtube.com/watch?v={candidate.video_id}\n".encode("utf-8")
