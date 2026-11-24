"""완료된 행과 등록 대상을 트랜잭션 단위로 보관한다."""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock, Timeout


class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL,
                    created TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    playlist TEXT, creating INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tracks (
                    job TEXT NOT NULL REFERENCES jobs(id), seq INTEGER NOT NULL,
                    artist TEXT NOT NULL, title TEXT NOT NULL, album TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Pending', query TEXT NOT NULL DEFAULT '',
                    candidates TEXT NOT NULL DEFAULT '[]', video TEXT,
                    delivery TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(job, seq)
                );
            """)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def lock(self, job: str) -> Iterator[None]:
        # OS 잠금은 강제 종료 시에도 풀리므로 여러 브라우저 탭에서 중복 작업하지 않는다.
        try:
            with FileLock(str(self.path.parent / f"{job}.lock"), timeout=0):
                yield
        except Timeout:
            raise ValueError("다른 탭에서 이 작업을 처리 중입니다. 완료 후 다시 시도해 주세요.") from None

    def create(self, label: str, tracks: Sequence[tuple[str, str, str]]) -> str:
        job = uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO jobs(id, label) VALUES (?,?)", (job, label))
            db.executemany("INSERT INTO tracks(job, seq, artist, title, album) VALUES (?,?,?,?,?)",
                           ((job, i, *row) for i, row in enumerate(tracks, 1)))
        return job

    def jobs(self) -> list[sqlite3.Row]:
        with self.db() as db:
            return db.execute("SELECT * FROM jobs ORDER BY rowid DESC").fetchall()

    def job(self, job: str) -> sqlite3.Row:
        with self.db() as db:
            return db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()

    def tracks(self, job: str) -> list[sqlite3.Row]:
        with self.db() as db:
            return db.execute("SELECT * FROM tracks WHERE job=? ORDER BY seq", (job,)).fetchall()

    def result(self, job: str, seq: int, status: str, candidates: list[dict[str, Any]], query: str = "") -> None:
        selected = candidates[0]["videoId"] if status == "High" else None
        with self.db() as db:
            db.execute("""UPDATE tracks SET status=?, candidates=?, video=?, query=?
                          WHERE job=? AND seq=? AND delivery='pending'""",
                       (status, json.dumps(candidates, ensure_ascii=False), selected, query, job, seq))

    def choose(self, job: str, seq: int, video: str) -> None:
        with self.db() as db:
            db.execute("UPDATE tracks SET status='Manual', video=? WHERE job=? AND seq=? AND delivery='pending'",
                       (video, job, seq))

    def choose_album_track(self, job: str, seq: int, video_id: str, title: str, channel: str) -> None:
        """앨범에서 직접 선택한 후보와 선택 상태를 함께 저장한다."""
        with self.lock(job), self.db() as db:
            row = db.execute("SELECT candidates FROM tracks WHERE job=? AND seq=? AND delivery='pending'",
                             (job, seq)).fetchone()
            if row is None:
                raise ValueError("현재 등록 상태를 확인한 뒤 음원 목록에서 입력 행을 다시 선택하세요.")
            other_candidates = [item for item in json.loads(row["candidates"]) if item["videoId"] != video_id]
            chosen = {"videoId": video_id, "title": title, "artist": channel, "score": None}
            db.execute("UPDATE tracks SET status='Manual', video=?, candidates=? WHERE job=? AND seq=?",
                       (video_id, json.dumps([chosen, *other_candidates], ensure_ascii=False), job, seq))

    def set_skipped(self, job: str, seq: int, *, skipped: bool) -> None:
        # 검색·선택 데이터는 원래대로 두고, 등록 대기와 건너뜀 사이에서만 전환한다.
        previous, new = ("pending", "skipped") if skipped else ("skipped", "pending")
        with self.lock(job), self.db() as db:
            cursor = db.execute("UPDATE tracks SET delivery=? WHERE job=? AND seq=? AND delivery=?",
                                (new, job, seq, previous))
            if cursor.rowcount != 1:
                raise ValueError("곡의 상태가 변경되었습니다. 새로고침한 후 곡을 다시 선택하세요.")

    def destination(self, job: str, playlist: str | None, creating: bool = False) -> None:
        with self.db() as db:
            db.execute("UPDATE jobs SET playlist=?, creating=? WHERE id=?", (playlist, int(creating), job))

    def delivered(self, job: str, results: list[tuple[str, int]]) -> None:
        with self.db() as db:
            db.executemany("UPDATE tracks SET delivery=? WHERE job=? AND seq=? AND delivery='pending'",
                           ((state, job, seq) for state, seq in results))
