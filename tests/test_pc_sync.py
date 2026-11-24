"""임시 bare 저장소와 두 PC 복제본으로 Git 동기화 명령을 검증한다."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "pc_sync.py"
ORIGIN = "https://github.com/junis2002/Youtube_Playlist.git"


class PcSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="PC 동기화 ")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.origin = self.folder / "origin.git"
        self.work = self.folder / "직장 PC"
        self.home = self.folder / "집 PC"
        self.cloud = self.folder / "cloud"
        self.environment = dict(os.environ)
        # 실제 계정, 네트워크 및 사용자 Git 설정에 의존하지 않는다.
        self.environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(self.folder / "gitconfig"),
            GIT_ALLOW_PROTOCOL="file",
            GIT_TERMINAL_PROMPT="0",
            PYTHONUTF8="1",
            GIT_AUTHOR_NAME="Sync test",
            GIT_AUTHOR_EMAIL="sync@example.invalid",
            GIT_COMMITTER_NAME="Sync test",
            GIT_COMMITTER_EMAIL="sync@example.invalid",
        )
        self.git(self.folder, "init", "--bare", "-b", "main", str(self.origin))
        self.git(self.folder, "init", "-b", "main", str(self.cloud))
        # origin의 원래 주소는 제품과 동일하고 실제 전송은 임시 bare 저장소로만 간다.
        self.git(self.folder, "config", "--global", f"url.{self.origin.as_uri()}.insteadOf", ORIGIN)
        self.git(self.cloud, "remote", "add", "origin", ORIGIN)
        self.first = self.commit(self.cloud, "README.md", "최초 배포본")
        self.git(self.cloud, "push", "origin", "main")
        for pc in (self.work, self.home):
            self.git(self.folder, "clone", ORIGIN, str(pc))

    def invoke(self, cwd: Path, *command: str, success: bool = True) -> str:
        env = self.environment.copy()
        env["COMPUTERNAME"] = {self.work: "EXAMPLE-WORK-PC", self.home: "EXAMPLE-HOME-PC"}.get(cwd, "CLOUD")
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30)
        details = f"{command} -> {result.returncode}\n{result.stdout}\n{result.stderr}"
        if success:
            self.assertEqual(result.returncode, 0, details)
        else:
            self.assertNotEqual(result.returncode, 0, details)
        return result.stdout.strip()

    def git(self, cwd: Path, *args: str) -> str:
        return self.invoke(cwd, "git", *args)

    def sync(self, cwd: Path, *args: str, success: bool = True) -> str:
        return self.invoke(cwd, sys.executable, str(SCRIPT), *args, success=success)

    def commit(self, pc: Path, name: str, content: str) -> str:
        (pc / name).write_text(content + "\n", encoding="utf-8")
        self.git(pc, "add", "--", name)
        self.git(pc, "commit", "-m", name)
        return self.git(pc, "rev-parse", "HEAD")

    def status(self) -> dict[str, Any]:
        return json.loads(self.sync(self.cloud, "status", "--json"))

    def refs(self) -> str:
        return self.git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)")

    def assert_pc(self, pc: str, *, pull: str | None, push: str | None,
                  head: str | None, current: bool | None) -> None:
        entry = self.status()["pcs"][pc]
        self.assertEqual((entry["pull"], entry["push"], entry["head"], entry["matches_main"]),
                         (pull, push, head, current))

    def test_independent_receipts_are_visible_from_cloud(self) -> None:
        self.sync(self.work, "init", "work")
        self.sync(self.home, "init", "home")
        # clone 및 로컬 역할 지정만으로는 작업을 완료했다고 보고하지 않는다.
        for pc in ("work", "home"):
            self.assert_pc(pc, pull=None, push=None, head=None, current=None)
        self.sync(self.work, "pull")
        self.assert_pc("work", pull=self.first, push=None, head=self.first, current=True)
        scratch = self.work / "개인 작업 메모.txt"
        scratch.write_text("PC 전용 메모", encoding="utf-8")
        second = self.commit(self.work, "project.py", "x = 1")
        self.sync(self.work, "push")
        self.assertEqual(self.status()["main"], second)
        self.assert_pc("work", pull=self.first, push=second, head=second, current=True)
        self.assert_pc("home", pull=None, push=None, head=None, current=None)
        self.assertEqual(scratch.read_text(encoding="utf-8"), "PC 전용 메모")
        self.assertEqual(self.git(self.work, "ls-files", "--", scratch.name), "")
        self.assertEqual(self.git(self.origin, "ls-tree", "--name-only", "main", "--", scratch.name), "")

        self.sync(self.home, "pull")
        self.assertEqual(self.git(self.home, "rev-parse", "HEAD"), second)
        self.assert_pc("home", pull=second, push=None, head=second, current=True)
        third = self.commit(self.home, "other.py", "y = 2")
        self.sync(self.home, "push")
        self.assert_pc("home", pull=second, push=third, head=third, current=True)
        self.assert_pc("work", pull=self.first, push=second, head=second, current=False)
        self.sync(self.work, "pull")
        self.assert_pc("work", pull=third, push=second, head=third, current=True)
        self.assertEqual(self.git(self.cloud, "rev-parse", "HEAD"), self.first)

    def test_divergence_preserves_local_commit_and_remote_receipts(self) -> None:
        self.sync(self.work, "init", "work")
        self.sync(self.work, "pull")
        ours = self.commit(self.work, "work.txt", "직장 전용 커밋")
        theirs = self.commit(self.cloud, "cloud.txt", "동시 클라우드 커밋")
        self.git(self.cloud, "push", "origin", "main")
        refs = self.refs()
        for action in ("push", "pull"):
            with self.subTest(action=action):
                self.sync(self.work, action, success=False)
                self.assertEqual(self.git(self.work, "rev-parse", "HEAD"), ours)
                self.assertEqual(self.git(self.work, "status", "--porcelain"), "")
                self.assertEqual(self.refs(), refs)
        self.assertEqual(self.status()["main"], theirs)
        self.assert_pc("work", pull=self.first, push=None, head=self.first, current=False)

    def test_pull_with_an_unpushed_commit_leaves_all_remote_refs_intact(self) -> None:
        self.sync(self.work, "init", "work")
        self.sync(self.work, "pull")
        refs = self.refs()
        ahead = self.commit(self.work, "draft.txt", "로컬에서만 보관 중")
        self.sync(self.work, "pull", success=False)
        self.assertEqual(self.git(self.work, "rev-parse", "HEAD"), ahead)
        self.assertEqual(self.refs(), refs)
        self.invoke(self.origin, "git", "cat-file", "-e", ahead, success=False)

    def test_rejected_receipt_does_not_publish_code(self) -> None:
        self.sync(self.work, "init", "work")
        self.sync(self.work, "pull")
        self.commit(self.work, "work.txt", "새 코드")
        refs = self.refs()
        # Git 서버가 기록 브랜치 하나를 거절하면 main도 같은 요청에서 중단되어야 한다.
        hook = self.origin / "hooks" / "update"
        hook.write_bytes(b'#!/bin/sh\nif test "$1" = refs/heads/sync/work/push; then exit 1; fi\n')
        hook.chmod(0o755)
        self.sync(self.work, "push", success=False)
        self.assertEqual(self.refs(), refs)

    def test_dirty_worktree_and_index_cannot_update_receipts(self) -> None:
        self.sync(self.home, "init", "home")
        original = self.refs()
        path = self.home / "README.md"
        path.write_text("사용자가 편집 중\n", encoding="utf-8")
        for staged in (False, True):
            if staged:
                self.git(self.home, "add", "--", path.name)
            index = self.git(self.home, "write-tree")
            for action in ("pull", "push"):
                with self.subTest(staged=staged, action=action):
                    self.sync(self.home, action, success=False)
                    self.assertEqual(path.read_text(encoding="utf-8"), "사용자가 편집 중\n")
                    self.assertEqual(self.git(self.home, "write-tree"), index)
                    self.assertEqual(self.refs(), original)


if __name__ == "__main__":
    unittest.main()
