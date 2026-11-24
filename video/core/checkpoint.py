"""작업 입력, 행별 결과와 API 요청 횟수를 트랜잭션 단위로 보존한다."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, label TEXT NOT NULL, filters TEXT NOT NULL,
                    playlist TEXT, creating INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS items (
                    job INTEGER NOT NULL REFERENCES jobs(id), pos INTEGER NOT NULL,
                    original TEXT NOT NULL, query TEXT NOT NULL, status TEXT NOT NULL,
                    video TEXT, score REAL, candidates TEXT NOT NULL DEFAULT '[]',
                    registration TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(job,pos)
                );
                CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, units INTEGER NOT NULL);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def lock(self, job: int) -> FileLock:
        # 같은 작업을 두 탭에서 동시에 등록하는 일을 막는다.
        return FileLock(self.path.with_name(f"job-{job}.lock"), timeout=0)

    def new_job(self, label: str, items: list[tuple[str, str | None]], filters: dict[str, Any]) -> int:
        with self.connect() as db:
            job = db.execute("INSERT INTO jobs(label,filters) VALUES(?,?)",
                             (label, json.dumps(filters, ensure_ascii=False))).lastrowid
            assert job is not None
            db.executemany("""INSERT INTO items(job,pos,original,query,status,video,score)
                              VALUES(?,?,?,?,?,?,?)""",
                           [(job, i, query, query, "High" if vid else "pending", vid,
                             100.0 if vid else None) for i, (query, vid) in enumerate(items, 1)])
        return job

    def jobs(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY id DESC")]

    def job(self, job: int) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if row is None:
                raise ValueError("저장된 작업을 찾을 수 없습니다.")
            return dict(row)

    def items(self, job: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM items WHERE job=? ORDER BY pos", (job,))]

    def match(self, job: int, pos: int, query: str, candidates: list[dict[str, Any]],
              chosen: dict[str, Any] | None = None) -> None:
        top = chosen or next(iter(candidates), None)
        if chosen is not None:
            status = "Manual"
        elif top is None:
            status = "Not Found"
        else:
            status = "High" if top["score"] >= 80 else "Low"
        with self.connect() as db:
            db.execute("""UPDATE items SET query=?,status=?,video=?,score=?,candidates=?,
                          registration='pending' WHERE job=? AND pos=?
                          AND registration NOT IN ('added','duplicate','sending')""",
                       (query, status, top["id"] if top else None, top["score"] if top else None,
                        json.dumps(candidates, ensure_ascii=False), job, pos))

    def mark(self, job: int, pos: int, status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE items SET registration=? WHERE job=? AND pos=?", (status, job, pos))

    def set_target(self, job: int, playlist: str | None, creating: bool = False) -> None:
        with self.connect() as db:
            db.execute("UPDATE jobs SET playlist=?,creating=? WHERE id=?", (playlist, int(creating), job))

    def charge(self, day: str, units: int) -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO usage VALUES(?,?) ON CONFLICT(day)
                          DO UPDATE SET units=units+excluded.units""", (day, units))

    def usage(self, day: str) -> int:
        with self.connect() as db:
            row = db.execute("SELECT units FROM usage WHERE day=?", (day,)).fetchone()
            return int(row[0]) if row else 0
