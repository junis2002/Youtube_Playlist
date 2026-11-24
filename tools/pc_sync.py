"""Report the last successful per-PC sync with the YouTube_Playlist main branch."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


PCS = ("work", "home")
ACTIONS = ("pull", "push", "head")
MAIN = "refs/heads/main"
REPOSITORY = "junis2002/youtube_playlist"
PC_LABELS = {"work": "직장", "home": "집"}


class SyncError(Exception):
    """A user-facing sync error."""


def git(*args: str, allow_missing: bool = False) -> str | None:
    proc = subprocess.run(["git", *args], capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode == 0:
        return proc.stdout.strip()
    if allow_missing and proc.returncode == 1:
        return None
    # Some credential helpers and URL rewrites include user info in remote diagnostics.
    message = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", proc.stderr.strip())
    raise SyncError(message or f"Git 명령이 실패했습니다 (종료 코드 {proc.returncode}).")


def is_project_url(url: str) -> bool:
    if url.startswith("git@github.com:"):
        path = url.removeprefix("git@github.com:")
    else:
        parsed = urlsplit(url)
        if parsed.scheme not in ("https", "ssh") or parsed.hostname != "github.com":
            return False
        if parsed.query or parsed.fragment:
            return False
        path = parsed.path.lstrip("/")
    return path.rstrip("/").lower().removesuffix(".git") == REPOSITORY


def check_origin() -> None:
    if git("rev-parse", "--is-inside-work-tree") != "true":
        raise SyncError("프로젝트의 Git 작업 폴더에서 실행하세요.")
    # Validate the configured project before Git applies a trusted transport rewrite.
    fetch = git("config", "--get-all", "remote.origin.url", allow_missing=True)
    push = git("config", "--get-all", "remote.origin.pushurl", allow_missing=True)
    urls = [value for value in (fetch, push) if value is not None]
    if not fetch or not all(len(url.splitlines()) == 1 and is_project_url(url) for url in urls):
        raise SyncError("origin이 junis2002/Youtube_Playlist 한 곳을 가리키도록 설정하세요.")


def require_main() -> None:
    if git("symbolic-ref", "--quiet", "HEAD", allow_missing=True) != MAIN:
        raise SyncError("main 브랜치에서 실행하세요.")


def require_clean() -> None:
    require_main()
    if git("status", "--porcelain=v1", "--untracked-files=no", "--ignore-submodules=none"):
        raise SyncError("추적 파일이나 스테이징 영역에 변경이 있습니다. 변경을 정리한 뒤 다시 실행하세요.")
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
        if Path(git("rev-parse", "--git-path", marker)).exists():
            raise SyncError("진행 중인 Git 작업을 마무리한 뒤 다시 실행하세요.")


def role(explicit: str | None) -> str:
    saved = git("config", "--local", "--get", "playlist.pc", allow_missing=True)
    if saved and explicit and saved != explicit:
        raise SyncError("지정한 PC와 이 저장소에 설정된 PC가 다릅니다. 현재 PC 설정을 확인하세요.")
    pc = explicit or saved
    if pc not in PCS:
        raise SyncError("먼저 init work 또는 init home으로 이 PC의 역할을 설정하세요.")
    return pc


def marker(pc: str, action: str) -> str:
    return f"refs/heads/sync/{pc}/{action}"


def push_refs(sha: str, refs: list[str]) -> None:
    # No force, tags, or unrelated branches. A failed member rejects the atomic batch.
    git("push", "--atomic", "--no-follow-tags", "--recurse-submodules=no", "origin",
        *(f"{sha}:{ref}" for ref in refs))


def sync(action: str, pc: str) -> None:
    require_clean()
    local_sha = git("rev-parse", "--verify", "HEAD^{commit}")
    if action == "push":
        push_refs(local_sha, [MAIN, marker(pc, "push"), marker(pc, "head")])
        print(f"{PC_LABELS[pc]} PC의 main 푸시 및 보고 완료 · {local_sha}")
        return
    remote_ref = "refs/remotes/origin/main"
    git("fetch", "--no-tags", "--no-recurse-submodules", "origin", f"{MAIN}:{remote_ref}")
    remote_sha = git("rev-parse", "--verify", f"{remote_ref}^{{commit}}")
    if git("merge-base", "--is-ancestor", local_sha, remote_sha, allow_missing=True) is None:
        if git("merge-base", "--is-ancestor", remote_sha, local_sha, allow_missing=True) is not None:
            raise SyncError("로컬 main이 앞서 있습니다. 커밋을 검토하고 push한 뒤 pull하세요.")
        raise SyncError("로컬 main과 GitHub main이 갈라졌습니다. 커밋을 확인하고 분기를 정리하세요.")
    # A fetch may wait for credentials; the user may switch branches or edit meanwhile.
    require_clean()
    if git("rev-parse", "--verify", "HEAD^{commit}") != local_sha:
        raise SyncError("원격 조회 중 로컬 main이 변경됐습니다. 현재 커밋을 확인하고 다시 실행하세요.")
    git("merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", remote_sha)
    require_main()
    if git("rev-parse", "--verify", "HEAD^{commit}") != remote_sha:
        raise SyncError("동기화 도중 로컬 HEAD가 변경되어 PC 상태를 보고하지 않았습니다.")
    try:
        push_refs(remote_sha, [marker(pc, "pull"), marker(pc, "head")])
    except SyncError as exc:
        raise SyncError(f"로컬 main에 {remote_sha}을 적용했으나 PC 상태 보고를 확인하지 못했습니다. "
                        f"원격 상태를 조회하고 pull을 다시 실행하세요.\n{exc}") from exc
    print(f"{PC_LABELS[pc]} PC의 main 풀 및 보고 완료 · {remote_sha}")


def status(as_json: bool) -> None:
    names = [MAIN, *(marker(pc, action) for pc in PCS for action in ACTIONS)]
    response = git("ls-remote", "--refs", "origin", *names)
    refs = {}
    for line in response.splitlines():
        sha, ref = line.split("\t", 1)
        refs[ref] = sha
    main = refs.get(MAIN)
    pcs = {}
    for pc in PCS:
        reported = {action: refs.get(marker(pc, action)) for action in ACTIONS}
        head = reported["head"]
        pcs[pc] = {**reported, "matches_main": head == main if head and main else None}
    if as_json:
        print(json.dumps({"main": main, "pcs": pcs}, indent=2))
        return
    print(f"GitHub main  {main or '원격 브랜치 없음'}")
    for pc, result in pcs.items():
        relation = {True: "main과 일치", False: "main과 다름", None: "비교할 보고 없음"}[result["matches_main"]]
        print(f"\n{PC_LABELS[pc]} PC ({pc}) · 마지막 보고 기준 {relation}")
        for action in ACTIONS:
            print(f"  {action:<5}  {result[action] or '미보고'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub main과 각 PC의 마지막 동기화 상태를 관리합니다.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="이 체크아웃에 PC 역할 저장")
    init.add_argument("pc", choices=PCS)
    for command in ("pull", "push"):
        sub = commands.add_parser(command, help=f"main {command} 및 PC 상태 보고")
        sub.add_argument("--pc", choices=PCS)
    report = commands.add_parser("status", help="GitHub에서 두 PC의 마지막 보고 조회")
    report.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        check_origin()
        if args.command == "status":
            status(args.json)
        elif args.command == "init":
            require_main()
            git("config", "--local", "playlist.pc", args.pc)
            print(f"이 체크아웃을 {PC_LABELS[args.pc]} PC ({args.pc})로 설정했습니다.")
        else:
            sync(args.command, role(args.pc))
    except (SyncError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
