"""일반 유튜브의 검색, 검토, 등록을 PC 로컬에서 진행하는 화면."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any

import pandas as pd
import streamlit as st
from filelock import Timeout

if __package__:
    from .core.auth import AuthError, get_service, save_client_secrets
    from .core.checkpoint import Store
    from .core.parser import build_inputs, read_file, video_id
    from .core.playlist import ALBUM_PLAYLIST_ID, AlbumTrack, PlaylistError, PlaylistInfo, Playlists, quick_links, quota_day
    from .core.register import publish
    from .core.searcher import Filters, search
else:
    from core.auth import AuthError, get_service, save_client_secrets
    from core.checkpoint import Store
    from core.parser import build_inputs, read_file, video_id
    from core.playlist import ALBUM_PLAYLIST_ID, AlbumTrack, PlaylistError, PlaylistInfo, Playlists, quick_links, quota_day
    from core.register import publish
    from core.searcher import Filters, search

ROOT = Path(__file__).resolve().parent
READY = {"High", "Manual"}
DONE = {"added", "duplicate"}
STATUS = {"pending": "대기", "High": "자동 확정", "Low": "검토 필요",
          "Not Found": "검색 실패", "Manual": "수동 확정"}
REGISTRATION = {"pending": "대기", "added": "등록 성공", "duplicate": "중복",
                "sending": "전송 확인 필요", "failed": "등록 실패"}


def filter_widgets() -> Filters:
    with st.expander("새 작업 검색 필터"):
        short = st.checkbox("60초 이하 영상 제외")
        left, right = st.columns(2)
        low = left.number_input("최소 길이(분)", min_value=0.0, step=0.5)
        high = right.number_input("최대 길이(분, 0은 제한 없음)", min_value=0.0, step=0.5)
        priority = st.selectbox("후보 정렬 기준", ["유사도", "조회수", "채널명 포함"])
        channel = st.text_input("우선 채널명", disabled=priority != "채널명 포함")
        excluded = st.text_input("제외할 단어(쉼표로 구분)")
    return Filters(short, low, high, priority, channel, excluded)


@st.cache_data(show_spinner=False, max_entries=4, ttl=1800)
def parsed_upload(name: str, data: bytes) -> pd.DataFrame:
    # 버튼을 클릭할 때마다 같은 엑셀을 다시 해석하지 않는다.
    return read_file(name, data)


def new_input(store: Store) -> None:
    file = st.file_uploader("입력 파일", type=["md", "csv", "xlsx", "txt"])
    if file is None:
        st.caption("TXT에는 한 줄에 하나씩 검색어나 영상 URL을 넣으세요.")
        return
    try:
        frame = parsed_upload(file.name, file.getvalue())
    except ValueError:
        # 파일 라이브러리 오류에 원시 행 내용이 포함될 수 있어 정해진 안내를 사용한다.
        st.warning("파일을 읽을 수 없습니다. 인코딩(UTF-8/CP949), 열 개수, 파일 형식을 확인하세요.")
        return
    except Exception:
        st.warning("파일을 읽지 못했습니다. 암호를 해제하고 첫 시트에 표가 있는지 확인하세요.")
        return
    preferred = [c for c in frame.columns if c.casefold() in
                 {"url", "영상 url", "영상제목", "영상 제목", "제목", "title", "검색어", "키워드", "채널명"}]
    columns = st.multiselect("검색 열(선택한 순서대로 조합)", frame.columns.tolist(),
                             default=preferred or [frame.columns[0]], key=f"in-cols-{file.file_id}")
    if not columns:
        st.info("검색할 열을 선택하세요.")
        return
    inputs = build_inputs(frame, columns)
    if not inputs:
        st.info("선택한 열에 검색어 또는 URL을 입력하세요.")
        return
    st.dataframe(frame.head(30), hide_index=True)
    lo, hi = st.columns(2)
    start = int(lo.number_input("시작 항목", 1, len(inputs), value=1, key=f"start-{file.file_id}"))
    stop = int(hi.number_input("끝 항목", 1, len(inputs), value=len(inputs), key=f"stop-{file.file_id}"))
    selected = inputs[start - 1:stop]
    unique: dict[tuple[str, str], tuple[str, str | None]] = {}
    for query, vid in selected:
        key = ("id", vid) if vid else ("query", " ".join(query.casefold().split()))
        unique.setdefault(key, (query, vid))
    dedup = st.checkbox("선택 구간에서 중복 제거", value=True)
    st.caption(f"전체 {len(inputs):,}개 · 선택 {len(selected):,}개 · 구간 내 중복 {len(selected)-len(unique):,}개")
    filters = filter_widgets()
    valid_range = not filters.max_minutes or filters.max_minutes >= filters.min_minutes
    if not valid_range or not selected:
        st.warning("시작과 끝, 최소·최대 길이의 순서를 확인하세요.")
    if st.button("새 작업 저장", disabled=not selected or not valid_range):
        job = store.new_job(file.name, list(unique.values()) if dedup else selected, asdict(filters))
        st.session_state["video_job_id"] = job
        st.rerun()


def search_pending(store: Store, job: int, filters: Filters) -> None:
    with store.lock(job):
        pending = [r for r in store.items(job) if r["status"] == "pending"]
        bar = st.progress(0.0)
        start = monotonic()
        for index, row in enumerate(pending):
            eta = (monotonic() - start) / index * (len(pending) - index) if index else None
            text = f"{index + 1}/{len(pending)} · {row['query']}"
            if eta is not None:
                text += f" · 약 {eta:.0f}초 남음"
            bar.progress(index / len(pending), text=text)
            try:
                candidates = search(row["query"], filters)
            except Exception:
                st.warning("검색 서비스가 응답하지 않았습니다. 네트워크와 yt-dlp 버전을 확인한 후 ‘이어서 검색’을 누르세요.")
                return
            store.match(job, row["pos"], row["query"], candidates)
        bar.progress(1.0, text="검색 완료")


def export_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = ["순번", "원래 입력", "검색어", "판정", "점수", "영상 ID", "후보 제목", "채널", "영상 링크", "등록"]
    values = []
    for row in rows:
        candidate = next((c for c in json.loads(row["candidates"]) if c["id"] == row["video"]), {})
        values.append([row["pos"], row["original"], row["query"], STATUS[row["status"]], row["score"],
                       row["video"], candidate.get("title"), candidate.get("channel"),
                       f"https://www.youtube.com/watch?v={row['video']}" if row["video"] else "",
                       REGISTRATION[row["registration"]]])
    return pd.DataFrame(values, columns=columns)


def download_report(frame: pd.DataFrame, prefix: str, name: str) -> None:
    # 엑셀에서 외부 입력을 수식으로 실행하지 않도록 보호한다.
    def safe(value: Any) -> str:
        text = str(value)
        return "'" + text if text.lstrip().startswith(("=", "@", "-", "+")) else text

    frame = frame.fillna("").apply(lambda column: column.map(safe))
    csv = frame.to_csv(index=False).encode("utf-8-sig")
    def line(values: Any) -> str:
        return "| " + " | ".join(str(v).replace("\\", "\\\\").replace("|", r"\|")
                                 .replace("\r", " ").replace("\n", " ") for v in values) + " |"

    md = "\n".join([line(frame.columns), line(["---"] * len(frame.columns)),
                    *(line(row) for row in frame.itertuples(index=False, name=None))])
    left, right = st.columns(2)
    left.download_button(f"{name} CSV", csv, f"{prefix}.csv", "text/csv")
    right.download_button(f"{name} MD", md.encode("utf-8"), f"{prefix}.md", "text/markdown")


def review(store: Store, job: int, rows: list[dict[str, Any]], filters: Filters) -> None:
    # 등록 전 후보만 변경할 수 있다. 응답이 유실된 행은 먼저 원격 상태를 확인한다.
    editable = [r for r in rows if r["registration"] not in DONE | {"sending"}]
    all_items = st.checkbox("자동 확정 항목도 표시", key=f"show-high-{job}")
    options = {r["pos"]: r for r in editable if all_items or r["status"] in {"Low", "Not Found"}
               or r["registration"] == "failed"}
    if not options:
        st.caption("검토할 항목이 없습니다.")
        return
    pos = st.selectbox("검토할 항목", list(options),
                       format_func=lambda p: f"{p}. {options[p]['original']}", key=f"review-{job}")
    row = options[pos]
    query = st.text_input("검색어 또는 영상 URL 수정", value=row["query"], key=f"query-{job}-{pos}")
    if st.button("이 항목만 재검색", disabled=not query.strip()):
        with store.lock(job):
            vid = video_id(query)
            if vid:
                direct = {"id": vid, "title": query, "channel": "", "score": 100, "duration": None,
                          "webpage_url": f"https://www.youtube.com/watch?v={vid}"}
                store.match(job, pos, query.strip(), [direct], direct)
            else:
                try:
                    candidates = search(query.strip(), filters)
                except Exception:
                    st.warning("검색에 연결할 수 없습니다. 잠시 후 다시 시도하세요.")
                    return
                store.match(job, pos, query.strip(), candidates)
        st.rerun()
    candidates = json.loads(row["candidates"])
    if candidates:
        def label(i: int) -> str:
            c = candidates[i]
            secs = c.get("duration")
            duration = f"{int(secs)//60}:{int(secs)%60:02}" if secs is not None else "길이 미상"
            score = f"{c['score']}점" if c.get("score") is not None else "수동 선택"
            return f"{c['title']} · {c['channel']} · {duration} · {score}"

        choice = st.radio("후보 영상", range(len(candidates)), format_func=label,
                          key=f"choice-{job}-{pos}-{row['query']}")
        candidate = candidates[choice]
        if candidate.get("thumbnail"):
            st.image(candidate["thumbnail"], width=160)
        st.link_button("YouTube에서 후보 확인", candidate["webpage_url"])
        if st.button("이 영상으로 확정"):
            with store.lock(job):
                store.match(job, pos, row["query"], candidates, candidate)
            st.rerun()


def account() -> Any:
    uploaded = st.file_uploader("데스크톱 OAuth 클라이언트 JSON", type="json", key="oauth-upload")
    if uploaded and st.button("클라이언트 파일 저장"):
        save_client_secrets(ROOT, uploaded.getvalue())
        st.session_state.pop("video_api_preview", None)
        st.success("클라이언트 설정을 저장했습니다.")
    login = st.button("Google 계정 로그인(OAuth)")
    if login:
        st.session_state.pop("video_api_preview", None)
    service = get_service(ROOT, login=login)
    if service is None:
        st.info("client_secrets.json을 앱 폴더에 두거나 업로드한 뒤 로그인하세요. 발급 방법은 README를 참고하세요.")
    else:
        st.success("이 PC의 YouTube 계정 인증을 불러왔습니다.")
    return service


@dataclass
class ApiPreview:
    token_version: tuple[int, ...] | None
    channel: str = ""
    choices: dict[str, PlaylistInfo] | None = None
    # 중복 조회를 줄이되 토큰 교체와 사용자 새로고침 시 다시 조회한다.
    details: dict[str, PlaylistInfo | None] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def preview_cache() -> ApiPreview:
    try:
        stat = (ROOT / "token.json").stat()
        version = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    except FileNotFoundError:
        version = None
    cache = st.session_state.get("video_api_preview")
    if cache is None or cache.token_version != version:
        cache = ApiPreview(version)
        st.session_state["video_api_preview"] = cache
    return cache


def destination_preview(api: Playlists, cache: ApiPreview, playlist_id: str) -> tuple[str, str]:
    if playlist_id not in cache.details:
        try:
            cache.details[playlist_id] = api.playlist_info(playlist_id)
        except PlaylistError as error:
            cache.details[playlist_id] = None
            cache.errors[playlist_id] = str(error)
    if message := cache.errors.get(playlist_id):
        st.warning(message)
    info = cache.details[playlist_id]
    return ((info.title if info and info.title else "제목 확인 불가") + f" ({playlist_id})",
            info.privacy if info and info.privacy else "확인 불가")


def api_panel(store: Store, job: int, rows: list[dict[str, Any]], service: Any) -> None:
    today = quota_day()
    remaining = sum(1 for r in rows if r["status"] in READY and r["registration"] not in DONE)
    state = store.job(job)
    meter = st.empty()
    def update_meter() -> None:
        units = store.usage(today)
        meter.caption(f"태평양 날짜 {today} · 이 PC 예상 사용 {units:,}유닛 · 기본 10,000 기준 잔여 약 {max(0,10000-units):,}유닛")
    st.caption(f"등록 대기 {remaining:,}개 · 모두 새 영상이면 최대 {50 * remaining:,}유닛 + 조회 페이지당 1유닛 · 새 목록 생성 50유닛")
    if not service:
        update_meter()
        return
    api = Playlists(service, lambda n: store.charge(quota_day(), n))
    cache = preview_cache()
    if st.button("등록 정보 새로고침"):
        cache.channel = ""
        cache.details.clear()
        cache.errors.clear()
    if not cache.channel:
        try:
            cache.channel = api.channel_title() or "채널 이름 확인 불가"
        except PlaylistError as error:
            cache.channel = f"확인 불가 ({error})"
    target: str | None = None
    title, description, privacy = "", "", "private"
    mode = ""
    if state["playlist"]:
        st.link_button("대상 재생목록 열기", f"https://www.youtube.com/playlist?list={state['playlist']}")
    else:
        creating = bool(state["creating"])
        if creating:
            st.warning("이전 목록 생성 요청의 결과를 확인해야 합니다. 내 목록에서 생성된 목록을 선택하세요.")
            verify = st.checkbox("YouTube에서 이전 요청으로 생성된 목록이 없음을 확인했습니다.")
            if st.button("새 목록 생성 다시 허용", disabled=not verify):
                with store.lock(job):
                    if not store.job(job)["playlist"]:
                        store.set_target(job, None, False)
                st.rerun()
        mode = st.radio("등록 대상", ["기존 목록에 이어붙이기"] if creating else
                        ["새 재생목록", "기존 목록에 이어붙이기"], key=f"dest-{job}-{creating}")
        if mode == "새 재생목록":
            title = st.text_input("목록 제목", key=f"title-{job}")
            description = st.text_area("설명", key=f"description-{job}")
            privacy = st.selectbox("공개 설정", ["private", "unlisted", "public"], key=f"privacy-{job}")
        else:
            if st.button("내 재생목록 불러오기"):
                cache.choices = api.mine_info()
                cache.details.update(cache.choices)
                for playlist_id in cache.choices:
                    cache.errors.pop(playlist_id, None)
            if cache.choices:
                choices = cache.choices
                target = st.selectbox("내 재생목록", list(choices),
                                      format_func=lambda v: f"{choices[v].title or '제목 확인 불가'} ({v})")
            elif cache.choices == {}:
                st.info("계정에서 선택할 재생목록을 찾지 못했습니다.")
    active_id = state["playlist"] or target
    if active_id:
        display_name, display_privacy = destination_preview(api, cache, active_id)
    elif mode == "새 재생목록":
        display_name, display_privacy = title.strip() or "목록 제목 입력 대기", privacy
    else:
        display_name, display_privacy = "목록 선택 대기", "확인 대기"
    update_meter()
    st.subheader("등록 미리보기")
    st.text(f"로그인 채널  {cache.channel}\n대상 재생목록  {display_name}\n"
            f"공개 범위  {display_privacy}\n등록 대기  {remaining:,}개")
    ready = bool(remaining and (active_id or title.strip()))
    if st.button("등록 이어서 진행", type="primary", disabled=not ready):
        bar = st.progress(0.0)
        def progress(done: int, total: int, text: str) -> None:
            bar.progress(done/max(total, 1), text=text)
            spent = store.usage(quota_day())
            meter.caption(f"이 PC 오늘 예상 사용 {spent:,}유닛 · 기본 한도 기준 잔여 약 {max(0,10000-spent):,}유닛")
        try:
            publish(store, job, api, target, title, description, privacy, progress)
        except PlaylistError as error:
            st.session_state["video_notice"] = str(error)
        st.rerun()


def album_reader(store: Store, initial_id: str, *, on_choose: Callable[[AlbumTrack], None] | None = None,
                 choose_label: str = "선택한 수록곡으로 영상 작업 만들기") -> None:
    st.subheader("앨범 수록곡 조회")
    if on_choose is not None:
        playlist_id = initial_id
        st.caption(f"선택한 발매본의 앨범 재생목록 ID · {playlist_id}")
    else:
        playlist_id = st.text_input("앨범 재생목록 ID", value=initial_id, help="음원 앱에서 선택한 발매본의 OLAK5uy_… ID")
    valid = ALBUM_PLAYLIST_ID.fullmatch(playlist_id)
    if playlist_id and not valid:
        st.info("음원 앱에서 앨범을 선택한 뒤 ‘영상 앱에서 이 앨범 열기’를 사용하세요.")
    if st.button("기존 Google 연결로 수록곡 조회", disabled=not valid, type="primary"):
        st.session_state.pop("album_read_result", None)
        st.session_state.pop("album_export_choice", None)
        try:
            service = get_service(ROOT)
            if service is None:
                st.info("이 PC에 저장된 Google 연결이 없습니다.")
                return
            with st.spinner("YouTube Data API에서 앨범 수록곡을 읽는 중…"):
                api = Playlists(service, lambda n: store.charge(quota_day(), n))
                result = api.album_tracks(playlist_id)
            st.session_state.album_read_result = (playlist_id, result)
        except (AuthError, PlaylistError) as error:
            st.error(str(error))
    saved = st.session_state.get("album_read_result")
    if not saved or saved[0] != playlist_id:
        return
    result = saved[1]
    st.caption(f"공식 API 응답 {result.raw_count}개 · TXT로 내보낼 수 있는 항목 {len(result.entries)}개")
    if not result.entries:
        return
    st.dataframe([
        {"수록 순서": track.position + 1 if track.position is not None else None,
         "제목": track.title, "영상 소유 채널": track.channel, "영상 ID": track.video_id}
        for track in result.entries
    ], hide_index=True)
    selected = st.selectbox(
        "작업에 사용할 수록곡" if on_choose else "TXT로 내보낼 수록곡",
        range(len(result.entries)), index=None, key="album_export_choice",
        format_func=lambda i: f"{result.entries[i].title} · {result.entries[i].channel} · {result.entries[i].video_id}",
    )
    if selected is not None:
        track = result.entries[selected]
        st.download_button("선택한 수록곡 URL TXT 다운로드",
                           f"https://www.youtube.com/watch?v={track.video_id}\n".encode("utf-8"),
                           file_name="앨범-수록곡.txt", mime="text/plain")
        if on_choose is not None and st.button(choose_label, key="video_album_choose", type="primary"):
            on_choose(track)


def workspace(*, standalone: bool = False) -> None:
    """같은 저장소를 사용해 독립 앱과 통합 앱에서 영상 작업을 연다."""
    store = Store(ROOT / "data" / "checkpoint.sqlite3")
    if message := st.session_state.pop("video_notice", ""):
        st.warning(message)
    initial_id = st.query_params.get("album", "") if standalone else ""
    modes = ["빠른 링크 (무인증)", "내 계정에 등록 (공식 API)"]
    album_mode = "앨범 수록곡 조회 (공식 API)"
    if standalone:
        modes.append(album_mode)
    mode = st.radio("작업 방식", modes, index=2 if initial_id else 0, horizontal=True, key="video_mode")
    if mode == album_mode:
        album_reader(store, initial_id if ALBUM_PLAYLIST_ID.fullmatch(initial_id) else "")
        return
    service = None
    if mode.startswith("내 계정"):
        with st.expander("Google 계정 인증", expanded=not (ROOT / "token.json").exists()):
            try:
                service = account()
            except AuthError as error:
                st.error(str(error))
    jobs = store.jobs()
    with st.expander("파일 업로드 · 새 작업", expanded=not standalone or not jobs):
        new_input(store)
    if not jobs:
        return
    labels = {j["id"]: f"#{j['id']} · {j['label']}" for j in jobs}
    job = st.selectbox("작업 선택", list(labels), format_func=labels.get, key="video_job_id")
    state = store.job(job)
    filters = Filters(**json.loads(state["filters"]))
    rows = store.items(job)
    summary = Counter(r["status"] for r in rows)
    st.caption(f"총 {len(rows):,}개 · 검색 대기 {summary['pending']:,} · 자동 확정 {summary['High']:,}"
               f" · 수동 확정 {summary['Manual']:,} · 검토 필요 {summary['Low']:,} · 검색 실패 {summary['Not Found']:,}")
    find, check, output, report = st.tabs(["검색", "후보 검토", "재생목록", "결과 다운로드"])
    with find:
        st.caption("영상 URL은 바로 확정합니다. 검색 결과는 선택한 필터 적용 후 80점 이상이면 자동 확정합니다.")
        if st.button("이어서 검색", type="primary", disabled=not summary["pending"]):
            search_pending(store, job, filters)
            rows = store.items(job)
            if not any(r["status"] == "pending" for r in rows):
                st.rerun()
    with check:
        review(store, job, rows, filters)
    with output:
        registered = Counter(r["registration"] for r in rows)
        st.caption(f"등록 성공 {registered['added']:,} · 중복 {registered['duplicate']:,} · 등록 실패 {registered['failed']:,}"
                   f" · 전송 확인 필요 {registered['sending']:,}")
        if mode.startswith("빠른"):
            ids = [r["video"] for r in rows if r["status"] in READY]
            links = quick_links(ids)
            if links:
                st.caption("각 묶음을 열어 YouTube의 저장 메뉴에서 내 재생목록을 선택하세요. 자동 확정·수동 확정 항목만 포함합니다.")
                for i, url in enumerate(links, 1):
                    st.link_button(f"영상 묶음 {i} 열기 ({len(url.split('=')[-1].split(','))}개)", url)
            else:
                st.info("검색하거나 후보를 확정하면 최대 50개씩 묶음 링크가 표시됩니다.")
        else:
            api_panel(store, job, rows, service)
    with report:
        if state["playlist"]:
            st.markdown(f"[등록한 재생목록 보기](https://www.youtube.com/playlist?list={state['playlist']})")
        table = export_table(rows)
        st.dataframe(table, hide_index=True)
        download_report(table, f"video-{job}-all", "전체 결과")
        failed = table[table["판정"].isin(["검색 실패", "검토 필요"]) | table["등록"].isin(["등록 실패", "전송 확인 필요"])]
        download_report(failed, f"video-{job}-review", "실패·검토 목록")


def main() -> None:
    st.set_page_config(page_title="유튜브 영상 재생목록", layout="wide")
    st.title("유튜브 영상 재생목록")
    workspace(standalone=True)


if __name__ == "__main__":
    try:
        main()
    except Timeout:
        st.warning("다른 탭에서 이 작업을 진행하고 있습니다. 완료 후 다시 시도하세요.")
    except (AuthError, PlaylistError) as error:
        st.error(str(error))
    except (OSError, sqlite3.Error):
        st.error("로컬 파일을 열거나 저장할 수 없습니다. PC의 쓰기 권한, 여유 공간, 동기화 상태를 확인하세요.")
    except Exception:
        st.error("작업을 마치지 못했습니다. 입력과 인터넷 연결을 확인하고 저장된 작업을 다시 선택하세요.")
