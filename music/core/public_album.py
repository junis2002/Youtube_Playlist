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


# ytmusicapi.get_album은 MPRE로 시작하는 앨범 탐색 ID를 사용한다.
_ALBUM_ID = re.compile(r"MPRE[A-Za-z0-9_-]+")


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
                    options[browse_id] = AlbumOption(title, artists, browse_id)
            if options:
                return list(options.values())
        return []
    except Exception:
        raise PublicSearchError(f"YouTube Music {label} 응답을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.") from None


def load_album_tracks(option: AlbumOption) -> list[SongCandidate]:
    """선택된 앨범을 조회하고 실제 영상 ID가 있는 수록곡을 재생 순서로 돌려준다."""
    if not _ALBUM_ID.fullmatch(option.browse_id):
        raise ValueError("검색 결과에서 유효한 앨범을 선택해 주세요.")
    try:
        album = YTMusic(auth=None, language="en").get_album(option.browse_id)
        if not isinstance(album, dict) or not isinstance(album.get("tracks"), list):
            raise ValueError
        candidates: list[SongCandidate] = []
        album_name = _text(album.get("title")) or option.title
        album_artists = artist_names(album.get("artists")) or option.artists
        for track in album["tracks"]:
            if not isinstance(track, dict):
                raise ValueError
            if track.get("isAvailable") is False or track.get("videoType") not in (None, "", "MUSIC_VIDEO_TYPE_ATV"):
                continue
            if track.get("resultType") not in (None, "song"):
                continue
            title = _text(track.get("title"))
            video_id = valid_video_id(track.get("videoId"))
            if title and video_id:
                artists = artist_names(track.get("artists")) or album_artists
                candidates.append(SongCandidate(title, artists, album_name, video_id))
        return candidates
    except Exception:
        raise PublicSearchError("YouTube Music에서 선택한 앨범의 곡 목록을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
