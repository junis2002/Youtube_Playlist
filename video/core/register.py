"""등록 전후의 로컬 상태를 기록하고 원격 재생목록과 대조해 이어서 등록한다."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from filelock import Timeout

from .checkpoint import Store
from .playlist import PlaylistError, Playlists, Rejected, VideoUnavailable


def publish(
    store: Store, job: int, api: Playlists, target: str | None, title: str,
    description: str, privacy: str, progress: Callable[[int, int, str], None],
) -> str:
    try:
        with store.lock(job):
            return _publish_locked(store, job, api, target, title, description, privacy, progress)
    except Timeout:
        raise PlaylistError("다른 창에서 이 작업을 처리하고 있습니다. 그 창의 처리가 끝난 뒤 이어서 진행하세요.") from None


def _publish_locked(
    store: Store, job: int, api: Playlists, target: str | None, title: str,
    description: str, privacy: str, progress: Callable[[int, int, str], None],
) -> str:
    task = store.job(job)
    saved = task["playlist"]
    if saved and target and saved != target:
        raise PlaylistError("이 작업은 이미 다른 재생목록에 연결되어 있습니다. 새 대상으로 등록하려면 새 작업을 만드세요.")
    approved = [r for r in store.items(job) if r["status"] in {"High", "Manual"} and r["video"]]
    if not approved:
        raise PlaylistError("먼저 영상을 검색하거나 검토 화면에서 후보를 확정하세요.")
    playlist_id = saved or target
    if not playlist_id:
        if task["creating"]:
            raise PlaylistError("마지막 생성 요청의 결과를 확인할 수 없습니다. YouTube에서 재생목록을 확인하고 기존 목록으로 연결하세요.")
        # 서버 반영 직후 앱이 종료되면 새 목록을 자동으로 또 만들지 않는다.
        store.set_target(job, None, creating=True)
        try:
            playlist_id = api.create(title, description, privacy)
        except Rejected:
            store.set_target(job, None)
            raise
        store.set_target(job, playlist_id)
    # 이미 등록된 항목과 응답 유실로 완료 여부가 불명확한 항목을 매 실행마다 확인한다.
    existing = api.existing(playlist_id)
    if target and not saved:
        store.set_target(job, playlist_id)
    counts = Counter(r["registration"] for r in approved if r["registration"] in {"added", "duplicate"})
    total = len(approved)

    def report(item: str) -> None:
        done = counts["added"] + counts["duplicate"] + counts["failed"]
        progress(done, total, f"{item} · 등록 {counts['added']} · 중복 {counts['duplicate']} · 실패 {counts['failed']}")

    for row in approved:
        state, vid, pos = row["registration"], row["video"], row["pos"]
        if state in {"added", "duplicate"}:
            continue
        report(row["query"])
        if vid in existing:
            status = "added" if state == "sending" else "duplicate"
        else:
            store.mark(job, pos, "sending")
            try:
                api.add(playlist_id, vid)
            except VideoUnavailable:
                status = "failed"
            except Rejected:
                # 명시적 거절(할당량 포함)은 재시도 가능하다. 통신 실패는 sending을 보존한다.
                store.mark(job, pos, "pending")
                raise
            else:
                existing.add(vid)
                status = "added"
        store.mark(job, pos, status)
        counts[status] += 1
    report("등록 처리 완료")
    return playlist_id
