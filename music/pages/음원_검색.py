"""공개 YouTube Music 검색 및 영상 앱용 URL 전달 화면."""

import streamlit as st

from core.public_search import PublicSearchError, SearchResult, export_url, search_songs

REFERENCE_ID = "j1uXcHwLhHM"


def reference_query(artist: str, title: str) -> bool:
    # LOCAL-03에서 확인한 원곡 ID는 이 곡을 조회할 때만 비교한다.
    artist_key = "".join(artist.casefold().split())
    title_key = "".join(title.casefold().split())
    return artist_key in {"윤하", "younha"} and title_key in {
        "사건의지평선", "eventhorizon", "eventhorizon(사건의지평선)", "사건의지평선(eventhorizon)",
    }


def show_candidates(artist: str, title: str, result: SearchResult) -> None:
    st.subheader(f"{artist} · {title}")
    st.caption(" → ".join(result.attempts))
    if len(result.attempts) > 1:
        st.caption("입력한 검색어는 그대로 사용했습니다. 한국어·영어는 검색 응답의 언어입니다.")
    candidates = result.candidates
    known = reference_query(artist, title)
    if not candidates:
        st.info("이번 공개 검색에서 음원 후보를 찾지 못했습니다. 가수 또는 곡명의 다른 표기로 다시 검색해 보세요.")
        return

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

    selected = st.selectbox("영상 앱으로 전달할 후보", available, index=None, format_func=label,
                            key="public_song_choice", placeholder="곡명·가수·앨범을 확인한 뒤 선택하세요")
    if selected is not None:
        song = candidates[selected]
        st.download_button("선택한 URL TXT 다운로드", export_url(song),
                           file_name="LOCAL-06.txt" if known else "선택한-음원.txt",
                           mime="text/plain", type="primary")
        st.caption("영상 앱의 파일 업로드에서 내려받은 TXT를 선택하세요.")


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
        # 새 검색이 실패해도 이전 곡을 내려받는 일이 없도록 먼저 초기화한다.
        st.session_state.pop("public_song_results", None)
        st.session_state.pop("public_song_choice", None)
        try:
            with st.spinner("YouTube Music에서 검색 중…"):
                result = search_songs(artist, title)
        except (PublicSearchError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.public_song_results = (artist.strip(), title.strip(), result)
    if result := st.session_state.get("public_song_results"):
        show_candidates(*result)


if __name__ == "__main__":
    main()
