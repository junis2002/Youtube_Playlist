"""재생목록 대상을 고정한 뒤 확인된 곡을 최대 50개씩 등록한다."""

from collections.abc import Callable
from time import sleep
from typing import Any

from ytmusicapi import YTMusic

from .checkpoint import Checkpoint
from .matcher import read_with_retry


class PlaylistError(ValueError):
    """응답 본문과 계정 정보를 제외한 사용자 안내."""


def owned_playlists(client: YTMusic) -> dict[str, str]:
    try:
        items = read_with_retry(lambda: client.get_library_playlists(limit=None), delay=0)
        return {item["playlistId"]: item.get("title") or item["playlistId"]
                for item in items if item.get("playlistId") and item.get("owned")}
    except Exception:
        raise PlaylistError("목록을 읽지 못했습니다. 인증과 네트워크 연결을 확인해 주세요.") from None


def create(client: YTMusic, store: Checkpoint, job: str, title: str, description: str, privacy: str) -> None:
    if not title.strip() or any(c in title for c in "<>"):
        raise PlaylistError("제목을 입력하고 꺾쇠 문자를 빼 주세요.")
    current = store.job(job)
    if current["playlist"] or current["creating"]:
        raise PlaylistError("이미 대상이 있거나 생성 요청이 진행됐습니다. 기존 목록을 확인해 주세요.")
    # 응답을 받기 직전에 종료돼도 두 번째 목록을 자동 생성하지 않는다.
    store.destination(job, None, creating=True)
    try:
        playlist_id = client.create_playlist(title.strip(), description, privacy)
        if not isinstance(playlist_id, str) or not playlist_id:
            raise ValueError
    except Exception:
        raise PlaylistError("생성 결과를 확인할 수 없습니다. 내 재생목록을 새로 읽고 생성된 목록을 연결해 주세요.") from None
    store.destination(job, playlist_id)


def _remote_ids(client: YTMusic, playlist: str) -> set[str]:
    try:
        data = read_with_retry(lambda: client.get_playlist(playlist, limit=None), delay=0)
        tracks = data["tracks"]
        return {item["videoId"] for item in tracks if item.get("videoId")}
    except Exception:
        raise PlaylistError("대상 목록을 읽지 못했습니다. 같은 계정인지, 목록에 접근할 수 있는지 확인해 주세요.") from None


def register(client: YTMusic, store: Checkpoint, job: str,
             progress: Callable[[int, int, int, int, str], None],
             only_seq: int | None = None, delay: float = 0.35) -> None:
    playlist = store.job(job)["playlist"]
    if not playlist:
        raise PlaylistError("먼저 등록할 재생목록을 연결해 주세요.")
    rows = [r for r in store.tracks(job) if r["video"] and r["delivery"] == "pending"
            and (only_seq is None or r["seq"] == only_seq)]
    if not rows:
        return
    # 재개할 때 원격의 실제 상태를 먼저 읽어 마지막 저장 직전의 성공도 감지한다.
    existing = _remote_ids(client, playlist)
    added = skipped = 0
    for offset in range(0, len(rows), 50):
        batch = rows[offset:offset + 50]
        ids = list(dict.fromkeys(row["video"] for row in batch if row["video"] not in existing))
        confirmed: set[str] = set()
        if ids:
            sleep(delay)
            try:
                response: dict[str, Any] = client.add_playlist_items(playlist, videoIds=ids, duplicates=False)
                if response.get("status") != "STATUS_SUCCEEDED":
                    raise ValueError
                confirmed = {item["videoId"] for item in response.get("playlistEditResults", [])
                             if isinstance(item, dict) and item.get("videoId")}
            except Exception:
                # 부분 성공 또는 연결 끊김은 재시도 전에 실제 원격 목록으로 대조한다.
                pass
            if not set(ids).issubset(confirmed):
                confirmed |= set(ids) & _remote_ids(client, playlist)
        states = []
        for row in batch:
            video = row["video"]
            if video in existing:
                states.append(("existing", row["seq"]))
                skipped += 1
            elif video in confirmed:
                states.append(("added", row["seq"]))
                existing.add(video)
                added += 1
        store.delivered(job, states)
        progress(added + skipped, len(rows), added, skipped, batch[-1]["title"])
        if len(states) < len(batch):
            raise PlaylistError("일부 곡의 등록을 확인하지 못했습니다. 목록 편집 권한과 연결을 확인한 뒤 등록을 이어가세요.")
