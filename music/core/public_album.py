"""공개 앨범 검색과 사용자가 선택한 발매본의 수록곡 조회."""

import re
from dataclasses import dataclass

from ytmusicapi import YTMusic

from .public_search import PublicSearchError, SongCandidate, artist_names, valid_video_id


@dataclass(frozen=True)
class AlbumOption:
    title: str
    artists: tuple[str, ...]
    browse_id: str
    audio_playlist_id: str | None = None


@dataclass(frozen=True)
class AlbumTracks:
    tracks: list[SongCandidate]
    raw_count: int
    excluded: dict[str, int]
    audio_playlist_id: str | None
    id_conflict: bool


# ytmusicapi.get_album은 MPRE로 시작하는 앨범 탐색 ID를 사용한다.
_ALBUM_ID = re.compile(r"MPRE[A-Za-z0-9_-]+")
_AUDIO_PLAYLIST_ID = re.compile(r"OLAK5uy_[A-Za-z0-9_-]+")


def valid_audio_playlist_id(value: object) -> str | None:
    """서비스의 앨범 음원 목록 ID만 그대로 받아들인다."""
    return value if isinstance(value, str) and _AUDIO_PLAYLIST_ID.fullmatch(value) else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def find_albums(artist: str, album: str) -> list[AlbumOption]:
    """앨범 검색 후 필요하면 통합 검색에서 명시적인 앨범 행만 수집한다."""
    artist, album = artist.strip(), album.strip()
    if not artist or not album:
        raise ValueError("가수와 앨범명을 모두 입력해 주세요.")
    query = f"{artist} {album}"
    label = "앨범 검색"
    try:
        client = YTMusic(auth=None, language="en")
        for kind, label in (("albums", "앨범 검색"), (None, "통합 검색")):
            rows = client.search(query, filter=kind, limit=10)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError
            options: dict[str, AlbumOption] = {}
            for row in rows:
                browse_id = _text(row.get("browseId"))
                title = _text(row.get("title"))
                if row.get("resultType") != "album" or not title or not _ALBUM_ID.fullmatch(browse_id):
                    continue
                if browse_id not in options:
                    artists = artist_names(row.get("artists"))
                    if not artists and (artist_label := _text(row.get("artist"))):
                        artists = (artist_label,)
                    options[browse_id] = AlbumOption(
                        title, artists, browse_id, valid_audio_playlist_id(row.get("playlistId"))
                    )
            if options:
                return list(options.values())
        return []
    except Exception:
        raise PublicSearchError(f"YouTube Music {label} 응답을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.") from None


def _parse_track(row: object, album: str, album_artists: tuple[str, ...]) -> SongCandidate | str:
    # 제외 이유는 행마다 한 번만 세어 원본 트랙 수와 합계가 맞도록 한다.
    if not isinstance(row, dict):
        return "행 형식 오류"
    if row.get("isAvailable") is False:
        return "재생 불가"
    if row.get("videoType") not in (None, "", "MUSIC_VIDEO_TYPE_ATV"):
        return "음원 영상 유형 아님"
    if row.get("resultType") not in (None, "song"):
        return "곡 결과 유형 아님"
    title = _text(row.get("title"))
    if not title:
        return "곡명 누락"
    video_id = valid_video_id(row.get("videoId"))
    if not video_id:
        return "영상 ID 누락 또는 오류"
    return SongCandidate(title, artist_names(row.get("artists")) or album_artists, album, video_id)


def inspect_album_tracks(option: AlbumOption) -> AlbumTracks:
    """앨범 상세를 한 번 요청해 수록곡 판정과 음원 목록 ID 근거를 반환한다."""
    if not _ALBUM_ID.fullmatch(option.browse_id):
        raise ValueError("검색 결과에서 유효한 앨범을 선택해 주세요.")
    try:
        album = YTMusic(auth=None, language="en").get_album(option.browse_id)
        if not isinstance(album, dict) or not isinstance(album.get("tracks"), list):
            raise ValueError
        rows = album["tracks"]
        candidates: list[SongCandidate] = []
        excluded: dict[str, int] = {}
        album_name = _text(album.get("title")) or option.title
        album_artists = artist_names(album.get("artists")) or option.artists
        for row in rows:
            track = _parse_track(row, album_name, album_artists)
            if isinstance(track, str):
                excluded[track] = excluded.get(track, 0) + 1
            else:
                candidates.append(track)

        detail_id = valid_audio_playlist_id(album.get("audioPlaylistId"))
        search_id = valid_audio_playlist_id(option.audio_playlist_id)
        conflict = detail_id is not None and search_id is not None and detail_id != search_id
        return AlbumTracks(
            tracks=candidates, raw_count=len(rows), excluded=excluded,
            audio_playlist_id=None if conflict else (detail_id or search_id), id_conflict=conflict,
        )
    except Exception:
        raise PublicSearchError("YouTube Music에서 선택한 앨범의 곡 목록을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.") from None


def load_album_tracks(option: AlbumOption) -> list[SongCandidate]:
    """기존 호출자가 선택한 앨범의 재생 가능한 곡 목록을 계속 사용할 수 있게 한다."""
    return inspect_album_tracks(option).tracks
