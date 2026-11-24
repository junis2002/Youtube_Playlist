"""공개 카탈로그에서 음원을 조회하고 선택한 후보 한 건을 URL로 내보낸다."""

import re
from dataclasses import dataclass

from requests.exceptions import ConnectionError, Timeout
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


@dataclass(frozen=True)
class MixedRow:
    result_type: str
    video_type: str
    title: str
    video_id: str | None


@dataclass(frozen=True)
class SearchResult:
    candidates: list[SongCandidate]
    attempts: list[str]
    mixed_rows: list[MixedRow] | None = None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def valid_video_id(value: object) -> str | None:
    value = _text(value)
    return value if _VIDEO_ID.fullmatch(value) else None


def artist_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        name for item in value if isinstance(item, dict) and (name := _text(item.get("name")))
    )


def _song_candidates(rows: list[dict], *, songs_only: bool) -> list[SongCandidate]:
    candidates: list[SongCandidate] = []
    seen_ids: set[str] = set()
    for row in rows:
        kind = _text(row.get("resultType"))
        video_type = _text(row.get("videoType"))
        if row.get("isAvailable") is False or video_type not in {"", "MUSIC_VIDEO_TYPE_ATV"}:
            continue
        # Art Track은 혼합 응답에서 video로 분류되기도 한다. 앨범의 재생
        # 버튼에도 videoId가 있으므로 앨범·목록 등은 곡 후보로 바꾸지 않는다.
        is_song = kind == "song" or (not kind and songs_only)
        is_art_track = video_type == "MUSIC_VIDEO_TYPE_ATV" and kind in {"", "video"}
        if not (is_song or is_art_track):
            continue
        artists = artist_names(row.get("artists"))
        album = row.get("album")
        album_name = _text(album.get("name")) if isinstance(album, dict) else _text(album)
        video_id = valid_video_id(row.get("videoId"))
        if video_id is not None:
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)
        candidates.append(SongCandidate(
            title=_text(row.get("title")), artists=artists, album=album_name,
            video_id=video_id,
        ))
    return candidates


def search_songs(artist: str, title: str) -> SearchResult:
    """입력 검색어를 유지하고 내려받을 음원이 없을 때만 다른 공개 검색을 시도한다."""
    artist, title = artist.strip(), title.strip()
    if not artist or not title:
        raise ValueError("가수와 곡명을 모두 입력해 주세요.")
    query = f"{artist} {title}"
    clients: dict[str, YTMusic] = {}
    attempts: list[str] = []
    incomplete: list[SongCandidate] = []
    mixed_rows = None
    # ytmusicapi는 곡 검색의 섹션 제목을 응답 언어별로 판별한다.
    for language, kind, label in (
        ("ko", "songs", "한국어 곡 검색"),
        ("en", "songs", "영어 곡 검색"),
        ("en", None, "영어 통합 검색"),
    ):
        try:
            if language not in clients:
                clients[language] = YTMusic(auth=None, language=language)
            rows = clients[language].search(query, filter=kind, limit=10)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("unexpected search response")
            candidates = _song_candidates(rows, songs_only=kind == "songs")
        except (ConnectionError, Timeout):
            attempts.append(f"{label} · 연결 실패 또는 시간 초과")
            raise PublicSearchError(" / ".join(attempts)) from None
        except Exception:
            attempts.append(f"{label} · 검색 요청 또는 응답 처리 실패")
            raise PublicSearchError(" / ".join(attempts)) from None
        attempts.append(f"{label} · 응답 {len(rows)}건 · 음원 후보 {len(candidates)}건")
        if kind is None:
            mixed_rows = [
                MixedRow(
                    result_type=_text(row.get("resultType")),
                    video_type=_text(row.get("videoType")),
                    title=_text(row.get("title")) or _text(row.get("artist")),
                    video_id=valid_video_id(row.get("videoId")),
                ) for row in rows
            ]
        if any(song.video_id for song in candidates):
            return SearchResult(candidates, attempts, mixed_rows)
        if candidates and not incomplete:
            incomplete = candidates
    return SearchResult(incomplete, attempts, mixed_rows)


def export_url(candidate: SongCandidate) -> bytes:
    """영상 앱 TXT 파서가 읽는 한 줄짜리 일반 YouTube URL을 만든다."""
    if not candidate.video_id or not _VIDEO_ID.fullmatch(candidate.video_id):
        raise ValueError("유효한 영상 ID가 있는 후보를 선택해 주세요.")
    return f"https://www.youtube.com/watch?v={candidate.video_id}\n".encode("utf-8")
