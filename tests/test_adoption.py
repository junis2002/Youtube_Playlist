"""네트워크 없이 기존 ZIP → Git 전환의 보존·중단 조건을 검증한다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


def write(root: Path, name: str, data: bytes) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


class AdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.source = self.base / "임시 clone"
        self.target = self.base / "한글 예전 설치 폴더"
        self.source.mkdir()
        self.target.mkdir()
        self.original = b"historical private handoff\r\n"
        for root in (self.source, self.target):
            for app in ("music", "video"):
                write(root, f"{app}/app.py", b"# local app\n")
        write(self.source, "HANDOFF.md", b"# current public handoff\n")
        write(self.source, "tools/legacy_zip_hashes.json", json.dumps(
            {"HANDOFF.md": [hashlib.sha256(self.original).hexdigest()]}
        ).encode())
        self.script = self.source / "tools/adopt_checkout.py"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "tools/adopt_checkout.py", self.script)
        write(self.source, ".gitattributes", b"* text eol=lf\n")
        write(self.source, ".gitignore", b"*.json.local\n")
        git(self.source, "init", "-b", "main")
        git(self.source, "config", "core.autocrlf", "false")
        git(self.source, "add", ".")
        git(self.source, "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
            "commit", "-m", "Fixture")
        git(self.source, "remote", "add", "origin", "https://github.com/junis2002/Youtube_Playlist.git")
        git(self.source, "update-ref", "refs/remotes/origin/main", "HEAD")
        write(self.target, "HANDOFF.md", self.original)

    def run_tool(self, apply: bool = True) -> int:
        args = [sys.executable, str(self.script), "--project", str(self.target)]
        return subprocess.run(args + (["--apply"] if apply else []), capture_output=True).returncode

    def test_check_apply_and_rerun(self) -> None:
        for name in ("music/browser.json", "video/data/checkpoint.sqlite3", "Temp/export.txt",
                     "music/.venv/entry", "old.zip", "video/token.json"):
            write(self.target, name, b"keep bytes")
        self.assertEqual(self.run_tool(False), 0)
        self.assertFalse((self.target / ".git").exists())
        self.assertEqual((self.target / "HANDOFF.md").read_bytes(), self.original)
        self.assertEqual(self.run_tool(), 0)
        self.assertEqual(git(self.target, "rev-parse", "HEAD"), git(self.source, "rev-parse", "HEAD"))
        self.assertEqual(git(self.target, "remote", "get-url", "origin"),
                         b"https://github.com/junis2002/Youtube_Playlist.git\n")
        self.assertEqual(git(self.target, "rev-parse", "--abbrev-ref", "@{upstream}"), b"origin/main\n")
        self.assertEqual(git(self.target, "diff", "HEAD"), b"")
        backup, = self.base.glob("*-backup-*")
        self.assertEqual((backup / "HANDOFF.md").read_bytes(), self.original)
        for name in ("music/browser.json", "video/data/checkpoint.sqlite3", "Temp/export.txt",
                     "music/.venv/entry", "old.zip", "video/token.json"):
            self.assertEqual((self.target / name).read_bytes(), b"keep bytes")
        self.assertNotEqual(self.run_tool(), 0)
        self.assertEqual(len(list(self.base.glob("*-backup-*"))), 1)

    def test_unknown_local_edit_prevents_all_changes(self) -> None:
        write(self.target, "video/app.py", b"local edit")
        self.assertNotEqual(self.run_tool(), 0)
        self.assertFalse((self.target / ".git").exists())
        self.assertFalse((self.target / "tools").exists())
        self.assertEqual((self.target / "video/app.py").read_bytes(), b"local edit")
        self.assertEqual((self.target / "HANDOFF.md").read_bytes(), self.original)

    def test_dirty_clone_and_nested_git_are_rejected(self) -> None:
        write(self.source, "HANDOFF.md", b"unexpected edit")
        self.assertNotEqual(self.run_tool(), 0)
        git(self.source, "restore", "HANDOFF.md")
        git(self.target / "music", "init")
        self.assertNotEqual(self.run_tool(), 0)
        self.assertFalse((self.target / ".git").exists())
        self.assertEqual((self.target / "HANDOFF.md").read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
