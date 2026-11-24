"""기존 ZIP 설치 폴더에 Git main을 연결한다. 기본 동작은 사전 점검이다."""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime

ORIGIN = "https://github.com/junis2002/Youtube_Playlist.git"
LOCAL_FILES = (
    "browser*.json", "token*.json", "client_secret*.json", "credentials*.json", "secrets.toml",
    ".env", ".env.*", "*.sqlite*", "*.db", "*.db-*", "*.zip", "*.bundle",
)


class AdoptionError(Exception):
    """로컬 파일 내용을 노출하지 않는 전환 중단 메시지."""


def git(folder: Path, *args: str) -> bytes:
    # 오류 출력에 인증 URL 등 로컬 Git 설정이 섞이는 것을 피한다.
    command = subprocess.run(["git", "-C", str(folder), *args], capture_output=True)
    if command.returncode:
        raise AdoptionError(f"Git 작업 실패({args[0]}). Git 설치, main 체크아웃, 폴더 권한을 확인하세요.")
    return command.stdout


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def linked(path: Path) -> bool:
    # Windows의 정션 및 기타 reparse point도 다른 폴더를 가리킬 수 있다.
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) or path.is_symlink()
    except FileNotFoundError:
        return False


def guard(root: Path, rel: Path) -> Path:
    path = root
    for part in rel.parts:
        path = path / part
        if linked(path) or (path.is_dir() and os.path.lexists(path / ".git")):
            raise AdoptionError(f"링크 또는 중첩 Git 저장소를 확인하세요: {rel}")
    return path


@dataclass(frozen=True)
class Change:
    rel: Path
    previous: str | None


def plan(candidate: Path, target: Path, known: dict[str, list[str]]) -> list[Change]:
    changes: list[Change] = []
    conflicts: list[str] = []
    for entry in git(candidate, "ls-tree", "-rz", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, filename = entry.split(b"\t", 1)
        name = filename.decode("utf-8")
        rel = Path(name)
        mode = metadata.split()[0]
        if mode not in {b"100644", b"100755"} or rel.is_absolute() or ".." in rel.parts:
            raise AdoptionError(f"지원하지 않는 Git 파일 종류 또는 경로: {name}")
        # 추적 파일 목록만 순회한다. 실행 중 생성하는 인증·DB·가상환경에는 쓰지 않는다.
        if (any(p.lower() in {"data", ".data", ".git", "venv", "temp", "local-reports"} or p.lower().startswith(".venv")
                for p in rel.parts) or any(fnmatchcase(rel.name.lower(), pat) for pat in LOCAL_FILES)):
            raise AdoptionError(f"PC별 데이터 경로가 Git에 포함되어 중단: {name}")
        old = guard(target, rel)
        if old.exists() and not old.is_file():
            conflicts.append(name)
            continue
        previous = digest(old) if old.is_file() else None
        if previous == digest(candidate / rel):
            continue
        if previous is not None and previous not in known.get(name, []):
            conflicts.append(name)
            continue
        if any(parent.is_file() for parent in old.parents if parent.is_relative_to(target)):
            conflicts.append(name)
            continue
        changes.append(Change(rel, previous))
    if conflicts:
        raise AdoptionError("로컬 변경 또는 경로 충돌입니다. 수동 비교 후 재실행하세요.\n" + "\n".join(conflicts))
    return changes


def verify_previous(target: Path, change: Change) -> Path:
    old = guard(target, change.rel)
    if change.previous is None:
        unchanged = not os.path.lexists(old)
    else:
        unchanged = old.is_file() and digest(old) == change.previous
    if not unchanged:
        raise AdoptionError(f"점검 이후 로컬 파일이 바뀌었습니다: {change.rel}")
    return old


def apply(candidate: Path, target: Path, changes: list[Change]) -> Path | None:
    # 덮어쓸 원본을 형제 폴더에 먼저 보존하고 모든 SHA-256을 확인한 뒤 변경한다.
    backup = None
    for change in changes:
        original = verify_previous(target, change)
        if change.previous is None:
            continue
        if backup is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = Path(tempfile.mkdtemp(prefix=f"{target.name}-backup-{stamp}-", dir=target.parent))
            print(f"로컬 원본 백업: {backup}", flush=True)
        copy = backup / change.rel
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, copy)
        if digest(copy) != change.previous:
            raise AdoptionError(f"백업 검증 실패: {change.rel}")
    if os.path.lexists(target / ".git"):
        raise AdoptionError("이미 .git이 있습니다. 전환을 중단합니다.")
    for change in changes:
        destination = verify_previous(target, change)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate / change.rel, destination)
    # 대상과 같은 볼륨에 준비한 독립 .git을 마지막에 연결한다.
    os.rename(candidate / ".git", target / ".git")
    git(target, "diff", "--quiet", "HEAD", "--")
    return backup


def adopt(source: Path, target: Path, execute: bool) -> None:
    target = target.absolute()
    if any(linked(path) for path in [target, *target.parents]):
        raise AdoptionError("대상은 링크 또는 정션을 거치지 않는 실제 프로젝트 폴더여야 합니다.")
    source, target = source.resolve(strict=True), target.resolve(strict=True)
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise AdoptionError("임시 main 체크아웃은 기존 프로젝트 폴더 밖에 두세요.")
    if os.path.lexists(target / ".git"):
        raise AdoptionError("대상에 .git이 이미 있습니다. git status로 기존 저장소를 확인하세요.")
    inside = subprocess.run(["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"], capture_output=True)
    if inside.returncode == 0:
        raise AdoptionError("대상은 이미 Git 저장소 아래에 있습니다. 기존 작업 폴더를 확인하세요.")
    if any(not (target / folder / "app.py").is_file() for folder in ("music", "video")):
        raise AdoptionError("기존 music/app.py와 video/app.py가 있는 프로젝트 루트를 지정하세요.")
    origin = git(source, "config", "--get", "remote.origin.url").decode().strip().removesuffix(".git")
    head = git(source, "rev-parse", "HEAD").strip()
    if (origin.casefold() != ORIGIN.removesuffix(".git").casefold()
            or git(source, "symbolic-ref", "HEAD").strip() != b"refs/heads/main"
            or head != git(source, "rev-parse", "refs/remotes/origin/main").strip()
            or git(source, "status", "--porcelain", "--untracked-files=all")):
        raise AdoptionError("공식 HTTPS 주소에서 새로 복제한 깨끗한 main 체크아웃을 사용하세요.")

    with tempfile.TemporaryDirectory(prefix=".playlist-stage-", dir=target.parent) as stage:
        candidate = Path(stage) / "repo"
        # 로컬 복제로 완결된 객체 저장소를 만든다. 원격 서버로 보내는 요청은 없다.
        git(Path(stage), "clone", "--no-local", "--no-tags", "--single-branch", "--branch", "main",
            "--", str(source), str(candidate))
        if git(candidate, "rev-parse", "HEAD").strip() != head:
            raise AdoptionError("전환 준비 중 main이 바뀌었습니다. 사전 점검을 다시 실행하세요.")
        git(candidate, "remote", "set-url", "origin", ORIGIN)
        known = json.loads((candidate / "tools/legacy_zip_hashes.json").read_text(encoding="utf-8"))
        changes = plan(candidate, target, known)
        print(f"main {head.decode()[:12]} · 새 파일 {sum(c.previous is None for c in changes)}개 · "
              f"백업 후 갱신 {sum(c.previous is not None for c in changes)}개")
        if execute:
            # 다른 전환 작업이 같은 폴더의 내용을 동시에 바꾸지 못하게 한다.
            lock = target.parent / f".{target.name}.git-adoption-lock"
            try:
                lock.mkdir()
            except FileExistsError:
                raise AdoptionError(f"전환 잠금이 있습니다. 다른 전환 프로세스의 종료 여부를 확인하세요: {lock}") from None
            try:
                apply(candidate, target, changes)
            finally:
                lock.rmdir()
            print(f"Git 전환 완료: {target}")
        else:
            print("사전 점검 통과. 앱을 모두 종료한 뒤 같은 명령에 --apply를 추가하세요.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="기존 ZIP 설치 프로젝트 루트")
    parser.add_argument("--apply", action="store_true", help="점검 후 Git 연결 적용")
    args = parser.parse_args()
    try:
        adopt(Path(__file__).resolve().parents[1], args.project, args.apply)
    except (AdoptionError, OSError, ValueError) as exc:
        print(f"중단: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
