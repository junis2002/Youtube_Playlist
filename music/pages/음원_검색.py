"""공개 YouTube Music 검색 및 영상 앱용 URL 전달 화면."""

from collections import Counter

import streamlit as st

from core.public_album import find_albums, load_album_tracks
from core.public_search import PublicSearchError, SearchResult, SongCandidate, export_url, search_songs

REFERENCE_ID = "j1uXcHwLhHM"


def reference_query(artist: str, title: str) -> bool:
    # LOCAL-03에서 확인한 원곡 ID는 이 곡을 조회할 때만 비교한다.
    artist_key = "".join(artist.casefold().split())
    title_key = "".join(title.casefold().split())
    return artist_key in {"윤하", "younha"} and title_key in {
        "사건의지평선", "eventhorizon", "eventhorizon(사건의지평선)", "사건의지평선(eventhorizon)",
    }


def show_songs(candidates: list[SongCandidate], known: bool, *, album_tracks: bool = False) -> None:
    """곡 검색과 앨범 트랙에 같은 표와 수동 선택 절차를 적용한다."""
    rows = []
    for index, candidate in enumerate(candidates, 1):
        rows.append({
            "순서": index, "곡명": candidate.title, "가수": ", ".join(candidate.artists),
            "앨범": candidate.album, "영상 ID": candidate.video_id,
            "미리 듣기": f"https://music.youtube.com/watch?v={candidate.video_id}" if candidate.video_id else None,
        })
    st.dataframe(rows, hide_index=True,
                 column_config={"미리 듣기": st.column_config.LinkColumn(display_text="듣기")})

    available = [index for index, candidate in enumerate(candidates) if candidate.video_id]
    if len(available) < len(candidates):
        st.warning(f"{len(candidates)-len(available)}개 후보에 유효한 영상 ID가 없어 URL을 내보낼 수 없습니다.")
    if known:
        positions = [str(i + 1) for i in available if candidates[i].video_id == REFERENCE_ID]
        if positions:
            st.success(f"원곡 기준 ID {REFERENCE_ID}와 일치하는 후보는 {', '.join(positions)}번입니다.")
        else:
            st.info(f"이번 후보 목록에서 원곡 기준 ID {REFERENCE_ID}와 일치하는 항목을 찾지 못했습니다.")
    if not available:
        return

    def label(index: int) -> str:
        song = candidates[index]
        return f"{index+1}. {song.title} · {', '.join(song.artists)} · {song.album} · {song.video_id}"

    selected = st.selectbox(
        "TXT로 내보낼 앨범 트랙" if album_tracks else "영상 앱으로 전달할 후보",
        available, index=None, format_func=label,
        key="public_track_choice" if album_tracks else "public_song_choice",
        placeholder="곡명·가수·앨범을 확인한 뒤 선택하세요",
    )
    if selected is not None:
        song = candidates[selected]
        st.download_button(
            "선택한 트랙 URL TXT 다운로드" if album_tracks else "선택한 URL TXT 다운로드",
            export_url(song), file_name="LOCAL-06.txt" if known else "선택한-음원.txt",
            mime="text/plain", type="primary",
            key="public_track_download" if album_tracks else "public_song_download",
        )
        st.caption("영상 앱의 파일 업로드에서 내려받은 TXT를 선택하세요.")


def show_mixed(result: SearchResult, known: bool) -> None:
    mixed = result.mixed_rows
    if mixed is None:
        return
    if known:
        matches = [str(i) for i, row in enumerate(mixed, 1) if row.video_id == REFERENCE_ID]
        if matches:
            message = f"원곡 기준 ID {REFERENCE_ID}가 통합 검색 응답의 {', '.join(matches)}번 행에 있습니다."
            if not any(song.video_id == REFERENCE_ID for song in result.candidates):
                message += " 이 ID는 결과 유형 확인용으로만 표시됩니다."
            st.info(message)
        else:
            st.info(f"통합 검색 응답 {len(mixed)}행에서 원곡 기준 ID {REFERENCE_ID}를 찾지 못했습니다.")
    with st.expander("통합 검색 원본 행"):
        if mixed:
            result_types = Counter(row.result_type or "알 수 없음" for row in mixed)
            video_types = Counter(row.video_type or "알 수 없음" for row in mixed)
            for label, counts in (("resultType", result_types), ("videoType", video_types)):
                groups = " · ".join(f"{name} {count}건" for name, count in counts.items())
                st.caption(f"{label} (전체 {len(mixed)}행) — {groups}")
            st.dataframe([
                {"행": i, "제목": row.title, "결과 유형 (resultType)": row.result_type,
                 "영상 유형 (videoType)": row.video_type, "영상 ID": row.video_id}
                for i, row in enumerate(mixed, 1)
            ], hide_index=True)
        else:
            st.caption("통합 검색 응답이 0행입니다.")


def clear_tracks() -> None:
    # 앨범을 바꾸면 이전에 선택한 트랙을 다시 내려받을 수 없게 한다.
    st.session_state.pop("public_album_tracks", None)
    st.session_state.pop("public_track_choice", None)


def clear_albums() -> None:
    clear_tracks()
    st.session_state.pop("public_album_options", None)
    st.session_state.pop("public_album_choice", None)


def album_search(artist: str, known: bool) -> None:
    with st.expander("앨범에서 트랙 찾기"):
        with st.form("public_album_search"):
            cols = st.columns(2)
            artist_query = cols[0].text_input("앨범 가수", artist, key="public_album_artist")
            album_query = cols[1].text_input("앨범명", key="public_album_title")
            submitted = st.form_submit_button("앨범 검색")
        if submitted:
            clear_albums()
            try:
                with st.spinner("앨범 검색 중…"):
                    st.session_state.public_album_options = find_albums(artist_query, album_query)
            except (PublicSearchError, ValueError) as exc:
                st.error(str(exc))

        options = st.session_state.get("public_album_options")
        if options is None:
            return
        if not options:
            st.info("선택 가능한 앨범 0개입니다. 가수나 앨범명을 확인해 주세요.")
            return
        st.caption(f"선택 가능한 앨범 {len(options)}개")
        choice = st.selectbox(
            "트랙을 확인할 앨범", range(len(options)), index=None,
            format_func=lambda i: f"{i+1}. {options[i].title} · {', '.join(options[i].artists)}",
            key="public_album_choice", placeholder="앨범을 선택하세요", on_change=clear_tracks,
        )
        if choice is None:
            return
        album = options[choice]
        if st.button("선택한 앨범의 트랙 불러오기"):
            clear_tracks()
            try:
                with st.spinner("앨범 트랙 조회 중…"):
                    tracks = load_album_tracks(album)
            except (PublicSearchError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.session_state.public_album_tracks = (album.browse_id, tracks)
        loaded = st.session_state.get("public_album_tracks")
        if loaded and loaded[0] == album.browse_id:
            tracks = loaded[1]
            st.caption(f"TXT로 내보낼 수 있는 트랙 {sum(1 for song in tracks if song.video_id)}개")
            if tracks:
                show_songs(tracks, known, album_tracks=True)
            else:
                st.info("선택한 앨범에 조회된 트랙이 없습니다.")


def show_candidates(artist: str, title: str, result: SearchResult) -> None:
    st.subheader(f"{artist} · {title}")
    st.caption(" → ".join(result.attempts))
    if len(result.attempts) > 1:
        st.caption("입력한 검색어는 그대로 사용했습니다. 한국어·영어는 검색 응답의 언어입니다.")
    known = reference_query(artist, title)
    show_mixed(result, known)
    if result.candidates:
        show_songs(result.candidates, known)
    else:
        st.info("이번 공개 검색에서 음원 후보를 찾지 못했습니다. 가수·곡명의 다른 표기나 앨범명으로 찾아보세요.")
    album_search(artist, known)


def main() -> None:
    st.set_page_config(page_title="음원 검색", layout="wide")
    st.title("음원 검색")
    st.caption("YouTube Music의 공개 음원을 찾아 영상 앱에 전달할 수 있습니다.")
    with st.form("public_song_search"):
        cols = st.columns(2)
        artist = cols[0].text_input("가수", value="윤하")
        title = cols[1].text_input("곡명", value="사건의 지평선")
        submitted = st.form_submit_button("음원 검색", type="primary")
    if submitted:
        # 새 검색이 실패해도 이전 곡이나 앨범을 내려받는 일이 없도록 먼저 초기화한다.
        clear_albums()
        for key in ("public_song_results", "public_song_choice", "public_album_artist", "public_album_title"):
            st.session_state.pop(key, None)
        try:
            with st.spinner("YouTube Music에서 검색 중…"):
                result = search_songs(artist, title)
        except ValueError as exc:
            st.error(str(exc))
        except PublicSearchError as exc:
            # 곡 검색이 실패해도 앨범 검색은 별도의 요청으로 진행할 수 있다.
            st.session_state.public_song_results = (artist.strip(), title.strip(), str(exc))
        else:
            st.session_state.public_song_results = (artist.strip(), title.strip(), result)
    if saved := st.session_state.get("public_song_results"):
        artist, title, outcome = saved
        if isinstance(outcome, str):
            st.error(outcome)
            album_search(artist, reference_query(artist, title))
        else:
            show_candidates(artist, title, outcome)


if __name__ == "__main__":
    main()
