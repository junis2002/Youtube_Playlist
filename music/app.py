"""로컬에서 실행하는 음원 목록 → YouTube Music 재생목록 앱."""

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zipfile import BadZipFile

import pandas as pd
import streamlit as st
from ytmusicapi import YTMusic

if __package__:
    from .ai_review_ui import show_track_review
    from .core import auth, matcher, parser, playlist
    from .core.checkpoint import Checkpoint
    from .core.public_search import valid_video_id
else:
    from ai_review_ui import show_track_review
    from core import auth, matcher, parser, playlist
    from core.checkpoint import Checkpoint
    from core.public_search import valid_video_id

ROOT = Path(__file__).resolve().parent
AUTH_FILE = ROOT / "browser.json"
STATUS = {"Pending": "미검색", "High": "자동 확정", "Low": "확인 필요", "Not Found": "검색 실패", "Manual": "수동 확정"}
DELIVERY = {"pending": "미등록", "added": "등록 성공", "existing": "이미 포함", "skipped": "건너뜀"}


def flash(message: str) -> None:
    st.session_state.music_notice = message
    st.rerun()


def auth_ui(*, auto_connect_saved: bool) -> YTMusic | None:
    stamp = AUTH_FILE.stat().st_mtime_ns if AUTH_FILE.is_file() else None
    changed = "music_auth_stamp" not in st.session_state or stamp != st.session_state.music_auth_stamp
    if changed:
        st.session_state.music_client = None
        st.session_state.music_auth_stamp = stamp
        st.session_state.music_auth_error = ""
        st.session_state.music_channel = ""
        st.session_state.pop("music_lists", None)
        st.session_state.pop("music_targets", None)
    # 통합 화면의 파일 가져오기와 공식 API 앨범 조회는 브라우저 인증이 필요 없다.
    retry = stamp is not None and st.button("저장된 인증 다시 확인")
    if retry or (changed and auto_connect_saved and stamp is not None):
        try:
            client, channel = auth.connect_account(AUTH_FILE)
        except auth.AuthError as exc:
            st.session_state.music_client = None
            st.session_state.music_channel = ""
            st.session_state.music_auth_error = str(exc)
        else:
            st.session_state.music_client = client
            st.session_state.music_channel = channel
            st.session_state.music_auth_error = ""
        st.session_state.pop("music_lists", None)
        st.session_state.pop("music_targets", None)

    if st.session_state.get("music_client") is not None:
        st.success(f"YouTube Music 연결 채널 · {st.session_state.get('music_channel') or '이름 미확인'}")
    elif st.session_state.get("music_auth_error"):
        st.warning(st.session_state.music_auth_error)
    else:
        st.info("기존 음원 계정으로 검색하거나 등록하려면 YouTube Music에 연결하세요.")
    with st.expander("브라우저 요청 헤더로 계정 연결", expanded=st.session_state.get("music_client") is None):
        st.markdown(
            "1. 이 PC의 Chrome 또는 Edge에서 [YouTube Music](https://music.youtube.com)에 로그인하세요.\n"
            "2. **F12 → Network**에서 필터에 `browse`를 입력한 뒤 **F5**를 누르세요.\n"
            "3. `browse` 요청을 눌러 **Headers → Request Headers → View source**에서 요청 헤더를 복사하세요.\n"
            "4. 아래에 붙여넣고 연결하세요. 요청 헤더에는 로그인 쿠키가 들어 있으므로 이 로컬 앱에서만 사용하세요."
        )
        # Chrome·Edge에서 붙여넣은 쿠키도 화면에 그대로 표시하지 않는다.
        st.html('<style>textarea[aria-label="Raw Request Headers"] {-webkit-text-security: disc;}</style>')
        with st.form("music_auth", clear_on_submit=True):
            raw = st.text_area("Raw Request Headers", height=125, placeholder="이 PC에서 복사한 요청 헤더")
            if st.form_submit_button("인증 파일 생성 및 연결 테스트"):
                try:
                    client, channel = auth.setup_account(AUTH_FILE, raw)
                except auth.AuthError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.music_client = client
                    st.session_state.music_channel = channel
                    st.session_state.pop("music_targets", None)
                    st.session_state.music_auth_stamp = AUTH_FILE.stat().st_mtime_ns
                    st.session_state.music_auth_error = ""
                    st.session_state.pop("music_lists", None)
                    flash("인증 파일을 저장하고 계정 연결을 확인했습니다.")
    return st.session_state.get("music_client")


def import_ui(store: Checkpoint) -> None:
    upload = st.file_uploader("음원 목록 업로드", type=["md", "csv", "xlsx", "txt"],
                              help="TXT는 한 줄에 가수 - 곡명을 입력하세요. Excel은 첫 번째 시트를 사용합니다.")
    if upload is None:
        return
    data = upload.getvalue()
    prefix = hashlib.sha256(data).hexdigest()[:16]
    try:
        frame = parser.parse_file(upload.name, data)
    except (ValueError, OSError, KeyError, BadZipFile, ImportError) as exc:
        st.error(str(exc) if type(exc) is ValueError else "파일을 읽을 수 없습니다. 형식과 내용을 확인해 주세요.")
        return
    labels = list(frame.columns)
    defaults = parser.guess_columns(labels)
    cols = st.columns(3)
    artist = cols[0].selectbox("가수 열", labels, labels.index(defaults[0]), key=prefix+"artist")
    title = cols[1].selectbox("곡명 열", labels, labels.index(defaults[1]), key=prefix+"title")
    albums = [None, *labels]
    album = cols[2].selectbox("앨범 열", albums, albums.index(defaults[2]), key=prefix+"album",
                              format_func=lambda x: x if x is not None else "사용 안 함")
    if artist == title or album is not None and album in (artist, title):
        st.warning("가수, 곡명, 앨범에 서로 다른 열을 지정하세요.")
        return
    tracks = parser.map_tracks(frame, artist, title, album)
    keys = tracks[["artist", "title"]].apply(lambda col: col.str.casefold())
    dup = keys.duplicated()
    st.write(f"전체 {len(frame):,}행 · 유효 음원 {len(tracks):,}곡 · 중복 {int(dup.sum()):,}곡 · 빈 가수/곡명 {len(frame)-len(tracks)}행")
    selected_artists = st.multiselect("가수 선택", list(tracks.artist.unique()), key=prefix+"artists")
    selected_albums = st.multiselect("앨범 선택", [a for a in tracks.album.unique() if a], key=prefix+"albums")
    if selected_artists:
        tracks = tracks[tracks.artist.isin(selected_artists)]
    if selected_albums:
        tracks = tracks[tracks.album.isin(selected_albums)]
    if st.checkbox("같은 가수·곡명 중복 제거", value=True):
        tracks = tracks.loc[~keys.loc[tracks.index].duplicated()]
    if tracks.empty:
        st.info("선택한 조건에 해당하는 음원이 없습니다.")
        return
    start, end = (1, 1) if len(tracks) == 1 else st.slider("필터 적용 후 실행 구간", 1, len(tracks),
                                                          (1, len(tracks)), key=f"{prefix}range{len(tracks)}")
    chosen = tracks.iloc[start-1:end]
    st.dataframe(chosen.rename(columns={"artist": "가수", "title": "곡명", "album": "앨범"}), hide_index=True)
    if st.button(f"선택한 {len(chosen):,}곡으로 작업 만들기", type="primary"):
        job = store.create(upload.name, list(chosen.itertuples(index=False, name=None)))
        st.session_state.music_next_job = job
        flash("작업을 저장했습니다. 검색 탭에서 시작할 수 있습니다.")


def eta(start: float, done: int, total: int) -> str:
    seconds = round((time.monotonic() - start) / max(1, done) * (total - done))
    return f"{seconds // 60}분 {seconds % 60}초"


def search_ui(store: Checkpoint, job: str, client: YTMusic | None, delay: float) -> None:
    rows = store.tracks(job)
    skipped = sum(r["delivery"] == "skipped" for r in rows)
    pending = sum(r["status"] == "Pending" and r["delivery"] == "pending" for r in rows)
    st.write(f"전체 {len(rows):,}곡 · 검색 완료 {len(rows)-pending-skipped:,}곡 · 남은 곡 {pending:,}곡 · 건너뜀 {skipped:,}곡")
    if not st.button("검색 시작 / 이어서 진행하기", disabled=client is None or not pending, type="primary"):
        return
    assert client is not None
    with store.lock(job):
        rows = [r for r in store.tracks(job) if r["delivery"] != "skipped"]
        todo = [r for r in rows if r["status"] == "Pending" and r["delivery"] == "pending"]
        started = time.monotonic()
        bar = st.progress(0.0)
        counts = {s: sum(r["status"] == s for r in rows) for s in ("High", "Low", "Not Found")}
        for i, row in enumerate(todo, 1):
            bar.progress((len(rows) - len(todo) + i - 1) / len(rows), text=f"검색 중 · {row['artist']} — {row['title']}")
            status, options = matcher.search_track(client, row["artist"], row["title"], delay=delay)
            store.result(job, row["seq"], status, options)
            counts[status] += 1
            bar.progress((len(rows) - len(todo) + i) / len(rows),
                         text=f"확정 {counts['High']} · 확인 필요 {counts['Low']} · 실패 {counts['Not Found']} · 남은 시간 약 {eta(started,i,len(todo))}")
    flash("검색을 완료했습니다. 검토 및 결과 탭에서 확인이 필요한 곡을 살펴보세요.")


def registration(store: Checkpoint, job: str, client: YTMusic, delay: float, only_seq: int | None = None) -> None:
    bar = st.progress(0.0)
    started = time.monotonic()

    def report(done: int, total: int, added: int, skipped: int, track: str) -> None:
        bar.progress(done / total, text=f"{track} · 등록 {added} · 이미 포함 {skipped} · {done}/{total} · 남은 시간 약 {eta(started,done,total)}")

    playlist.register(client, store, job, report, only_seq, delay)


def target_details(client: YTMusic | None, dest: str) -> tuple[str, str]:
    """같은 채널·목록에 대한 등록 미리보기는 세션 안에서 재사용한다."""
    cache = st.session_state.setdefault("music_targets", {})
    if dest not in cache and client is not None:
        title = st.session_state.get("music_lists", {}).get(dest, "")
        privacy = ""
        try:
            details = client.get_playlist(dest, limit=1)
            title = details.get("title") or title
            privacy = details.get("privacy") or ""
        except Exception:
            pass  # 오래된 작업에는 목록 ID만 있어도 이어서 등록할 수 있다.
        cache[dest] = (title, privacy)
    title, privacy = cache.get(dest, ("", ""))
    name = f"{title} ({dest})" if title else dest
    return f"연결된 목록 · {name}", privacy or "미확인"


def write_preview(target: str, privacy: str, ready: int) -> None:
    channel = st.session_state.get("music_channel") or "미확인"
    st.text(f"연결 채널 · {channel}\n대상 · {target}\n공개 범위 · {privacy}\n등록 예정 · {ready:,}곡")


def playlist_ui(store: Checkpoint, job: str, client: YTMusic | None, delay: float) -> None:
    current = store.job(job)
    dest = current["playlist"]
    ready = sum(bool(r["video"]) and r["delivery"] == "pending" for r in store.tracks(job))
    if dest:
        st.link_button("연결한 재생목록 열기", "https://music.youtube.com/playlist?" + urlencode({"list": dest}))
        write_preview(*target_details(client, dest), ready)
        if st.button(f"확정한 {ready:,}곡 등록 / 이어서 진행하기", disabled=client is None or ready == 0, type="primary"):
            assert client is not None
            with store.lock(job):
                registration(store, job, client, delay)
            flash("확정한 곡의 등록 결과를 저장했습니다.")
        return
    if current["creating"]:
        st.warning("직전 생성 요청의 결과를 확인해 주세요. 목록이 있으면 아래에서 연결하세요.")
        if st.checkbox("YouTube Music에서 새 목록이 생성되지 않은 것을 확인했습니다.", key=f"verify{job}"):
            if st.button("새 목록 생성 다시 허용"):
                with store.lock(job):
                    if not store.job(job)["playlist"]:
                        store.destination(job, None)
                st.rerun()
    mode = "기존 목록" if current["creating"] else st.radio("등록 대상", ["새 목록", "기존 목록"], horizontal=True)
    if mode == "새 목록":
        title = st.text_input("재생목록 제목", value=Path(current["label"]).stem, key=f"title{job}")
        description = st.text_area("설명", key=f"desc{job}")
        privacy = st.selectbox("공개 설정", ["PRIVATE", "UNLISTED", "PUBLIC"])
        write_preview(f"새 목록 · {title}", privacy, ready)
        if st.button("재생목록 생성 및 연결", disabled=client is None or not title.strip()):
            assert client is not None
            with store.lock(job):
                playlist.create(client, store, job, title, description, privacy)
                dest = store.job(job)["playlist"]
                st.session_state.setdefault("music_targets", {})[dest] = (title, privacy)
            flash("새 재생목록을 연결했습니다. 등록할 곡 수를 확인해 주세요.")
    else:
        if st.button("내 재생목록 불러오기", disabled=client is None):
            assert client is not None
            st.session_state.music_lists = playlist.owned_playlists(client)
        choices = st.session_state.get("music_lists")
        if choices:
            selected = st.selectbox("내 재생목록", list(choices), format_func=lambda k: f"{choices[k]} · {k}")
            if st.button("이 재생목록에 연결", disabled=client is None):
                with store.lock(job):
                    if store.job(job)["playlist"] is None:
                        store.destination(job, selected)
                flash("재생목록을 연결했습니다. 등록할 곡 수를 확인해 주세요.")
        elif choices is not None:
            st.info("현재 계정에서 소유한 재생목록이 없습니다.")


def review_ui(store: Checkpoint, job: str, rows: list[sqlite3.Row], client: YTMusic | None, delay: float) -> None:
    review = {r["seq"]: r for r in rows if r["status"] in ("Low", "Not Found") and r["delivery"] == "pending"}
    if not review:
        return
    seq = st.selectbox("확인할 곡", list(review), format_func=lambda n: f"{n}. {review[n]['artist']} — {review[n]['title']}", key=f"rev{job}")
    row = review[seq]
    query = st.text_input("재검색할 검색어", row["query"] or f"{row['artist']} {row['title']}", key=f"query{job}{seq}")
    if st.button("재검색", disabled=client is None or not query.strip()):
        assert client is not None
        with store.lock(job):
            _, options = matcher.search_track(client, row["artist"], row["title"], query=query, delay=delay)
            # 재검색은 자동 등록하지 않고 직접 고른 후보를 사용한다.
            store.result(job, seq, "Low" if options else "Not Found", options, query)
        flash("재검색 결과를 저장했습니다.")
    options = {c["videoId"]: c for c in json.loads(row["candidates"])}
    if not options:
        return
    show_track_review(row["artist"], row["title"], row["album"] or "",
                      ((vid, item["title"], item["artist"], "") for vid, item in options.items()),
                      context=f"music_{job}_{seq}")
    selected = st.radio("후보 선택", list(options), key=f"cand{job}{seq}", format_func=lambda v:
                        f"{options[v]['artist']} — {options[v]['title']} ({options[v]['score']:.0f}점)")
    st.link_button("선택한 후보 듣기", "https://music.youtube.com/watch?" + urlencode({"v": selected}))
    choose = st.button("이 후보 확정")
    dest = store.job(job)["playlist"]
    if dest:
        write_preview(*target_details(client, dest), 1)
    add = st.button("확정 후 재생목록에 바로 추가", disabled=client is None or not dest)
    if choose or add:
        with store.lock(job):
            store.choose(job, seq, selected)
            if add:
                assert client is not None
                registration(store, job, client, delay, seq)
        flash("선택한 후보의 처리 결과를 저장했습니다.")


def exports(rows: list[sqlite3.Row]) -> None:
    labels = ["번호", "가수", "곡명", "앨범", "매칭", "등록 상태", "선택한 영상 ID", "대표 후보", "점수", "검색어"]
    data = []
    for r in rows:
        candidates = json.loads(r["candidates"])
        chosen = next((c for c in candidates if c["videoId"] == r["video"]), candidates[0] if candidates else {})
        data.append((r["seq"], r["artist"], r["title"], r["album"], STATUS[r["status"]], DELIVERY[r["delivery"]],
                     r["video"] or "", chosen.get("title", ""), chosen.get("score"), r["query"]))
    frame = pd.DataFrame(data, columns=labels)
    st.dataframe(frame, hide_index=True)
    selection = st.radio("내보낼 항목", ["전체 결과", "검색 실패", "확인 필요 및 검색 실패"], horizontal=True)
    if selection != "전체 결과":
        statuses = ["검색 실패"] if selection == "검색 실패" else ["검색 실패", "확인 필요"]
        frame = frame[frame["매칭"].isin(statuses) & (frame["등록 상태"] != "건너뜀")]
    # 스프레드시트가 외부 제목을 수식으로 실행하지 않도록 보호한다.
    safe = frame.map(lambda v: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
    st.download_button("CSV 다운로드", safe.to_csv(index=False).encode("utf-8-sig"), f"{selection}.csv", "text/csv")
    def cell(value: Any) -> str:
        if pd.isna(value):
            return ""
        return str(value).replace("\\", "\\\\").replace("|", r"\|").replace("\r", " ").replace("\n", " ")
    lines = ["| " + " | ".join(labels) + " |", "| " + " | ".join("---" for _ in labels) + " |"]
    lines.extend("| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False, name=None))
    st.download_button("Markdown 다운로드", "\n".join(lines), f"{selection}.md", "text/markdown")


def skip_controls(store: Checkpoint, job: str, rows: list[sqlite3.Row]) -> None:
    with st.expander("곡 건너뛰기 · 다시 열기"):
        for state, select_label, action_label in (
            ("pending", "건너뛸 곡", "이 곡 건너뛰기"), ("skipped", "다시 열 곡", "이 곡 다시 열기"),
        ):
            options = {r["seq"]: f"{r['seq']}. {r['artist']} — {r['title']} · {STATUS[r['status']]}"
                       for r in rows if r["delivery"] == state}
            if not options:
                continue
            seq = st.selectbox(select_label, list(options), index=None, format_func=options.get,
                               key=f"music_disposition_{state}_{job}", placeholder="곡을 선택하세요")
            if st.button(action_label, key=f"music_apply_{state}_{job}", disabled=seq is None) and seq is not None:
                skip = state == "pending"
                store.set_skipped(job, seq, skipped=skip)
                flash(f"{seq}번 곡을 건너뛰었습니다." if skip else f"{seq}번 곡을 이전 상태로 다시 열었습니다.")


def results_ui(store: Checkpoint, job: str, client: YTMusic | None, delay: float) -> None:
    rows = store.tracks(job)
    cols = st.columns(6)
    cols[0].metric("전체 곡", len(rows))
    cols[1].metric("등록 성공", sum(r["delivery"] == "added" for r in rows))
    cols[2].metric("이미 포함", sum(r["delivery"] == "existing" for r in rows))
    cols[3].metric("확인 필요", sum(r["status"] == "Low" and r["delivery"] == "pending" for r in rows))
    cols[4].metric("검색 실패", sum(r["status"] == "Not Found" and r["delivery"] == "pending" for r in rows))
    cols[5].metric("건너뜀", sum(r["delivery"] == "skipped" for r in rows))
    dest = store.job(job)["playlist"]
    if dest:
        st.link_button("YouTube Music에서 결과 보기", "https://music.youtube.com/playlist?" + urlencode({"list": dest}))
    if any(r["delivery"] in ("pending", "skipped") for r in rows):
        skip_controls(store, job, rows)
    review_ui(store, job, rows, client, delay)
    exports(rows)


LookupCallback = Callable[[str, int, str, str, str], None]
PrepareCallback = Callable[[str], None]


def album_actions(store: Checkpoint, job: str, on_lookup: LookupCallback | None,
                  on_prepare: PrepareCallback | None) -> None:
    rows = store.tracks(job)
    pending = {r["seq"]: r for r in rows if r["delivery"] == "pending"}
    if on_lookup and pending:
        def label(seq: int) -> str:
            row = pending[seq]
            return " · ".join(str(v) for v in (seq, row["artist"], row["title"], row["album"]) if v != "")

        seq = st.selectbox("앨범에서 확인할 곡", list(pending), index=None, format_func=label,
                           key=f"music_lookup_row_{job}", placeholder="저장된 작업에서 한 곡을 선택하세요")
        if st.button("앨범에서 이 곡 찾기", disabled=seq is None) and seq is not None:
            row = pending[seq]
            on_lookup(job, seq, row["artist"], row["title"], row["album"])
    if on_prepare:
        ready = sum(r["status"] in ("High", "Manual") and valid_video_id(r["video"]) is not None
                    for r in pending.values())
        st.caption(f"영상 작업으로 보낼 수 있는 확정 곡 {ready:,}개")
        if st.button("확정한 곡으로 영상 작업 만들기", disabled=not ready):
            on_prepare(job)


def workspace(*, on_lookup: LookupCallback | None = None, on_prepare: PrepareCallback | None = None,
              auto_connect_saved: bool = False) -> None:
    """기존 음원 작업 DB와 파일 가져오기를 같은 작업 공간에서 연다."""
    store = Checkpoint(ROOT / ".data" / "music.sqlite3")
    jobs = {j["id"]: j for j in store.jobs()}
    if "music_next_job" in st.session_state:
        st.session_state.music_job = st.session_state.pop("music_next_job")
    job = st.selectbox("저장된 작업", list(jobs), key="music_job", format_func=lambda k:
                               f"{jobs[k]['label']} · {jobs[k]['created']} · {k[:6]}", placeholder="목록을 업로드하세요")
    delay = st.sidebar.slider("요청 간격 (초)", 0.2, 3.0, 0.35, 0.05)
    if message := st.session_state.pop("music_notice", None):
        st.success(message)
    upload, search, publish, results, account = st.tabs(["음원 업로드", "검색", "재생목록 등록", "검토 및 결과", "계정 연결"])
    with upload:
        import_ui(store)
        if job and (on_lookup or on_prepare):
            album_actions(store, job, on_lookup, on_prepare)
    with account:
        client = auth_ui(auto_connect_saved=auto_connect_saved)
    for tab, action in ((search, search_ui), (publish, playlist_ui), (results, results_ui)):
        with tab:
            if job:
                action(store, job, client, delay)
            else:
                st.info("음원 업로드 탭에서 작업을 먼저 만들어 주세요.")


def main() -> None:
    st.set_page_config(page_title="음원 재생목록", layout="wide")
    st.title("YouTube Music 재생목록")
    workspace(auto_connect_saved=True)


if __name__ == "__main__":
    try:
        main()
    except (auth.AuthError, matcher.SearchError, playlist.PlaylistError, ValueError) as exc:
        st.error(str(exc))
    except (sqlite3.Error, OSError):
        st.error("로컬 파일을 읽거나 저장하지 못했습니다. 폴더 권한과 남은 디스크 공간을 확인해 주세요.")
    except Exception:
        st.error("처리 중 오류가 발생했습니다. 파일 형식과 연결 상태를 확인하고 저장된 작업에서 이어서 진행하세요.")
