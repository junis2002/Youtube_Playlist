"""기존 음원·영상 작업을 한 Streamlit 세션에서 연다."""

import json
import sqlite3
from dataclasses import asdict

import streamlit as st
from filelock import Timeout

from music import app as music
from music.ai_review_ui import show_track_review
from music.core.public_search import valid_video_id
from music.pages.음원_검색 import album_search, clear_albums
from video import app as video
from video.core.playlist import AlbumTrack


def music_store() -> music.Checkpoint:
    return music.Checkpoint(music.ROOT / ".data" / "music.sqlite3")


def video_store() -> video.Store:
    return video.Store(video.ROOT / "data" / "checkpoint.sqlite3")


def go_to(view: str) -> None:
    st.session_state.next_workspace_view = view
    st.rerun()


def clear_album_context() -> None:
    clear_albums()
    for key in ("album_input_row", "public_album_artist", "public_album_title",
                "album_read_result", "album_export_choice"):
        st.session_state.pop(key, None)


def lookup_input(job: str, seq: int, artist: str, title: str, album: str) -> None:
    clear_album_context()
    st.session_state.album_input_row = (job, seq, artist, title, album)
    go_to("앨범 수록곡")


def open_video_job(source: str, label: str, tracks: list[tuple[str, str, str]]) -> None:
    job = video_store().confirmed_job(source, label, tracks, asdict(video.Filters()))
    st.session_state.video_job_id = job
    go_to("영상 목록")


def prepare_music_job(job: str) -> None:
    store = music_store()
    tracks = []
    for row in store.tracks(job):
        vid = valid_video_id(row["video"])
        if not vid or row["status"] not in {"High", "Manual"} or row["delivery"] != "pending":
            continue
        chosen = next((opt for opt in json.loads(row["candidates"]) if opt.get("videoId") == vid), {})
        tracks.append((vid, chosen.get("title") or row["title"], chosen.get("artist") or row["artist"]))
    open_video_job(f"music:{job}", store.job(job)["label"], tracks)


def album_workspace() -> None:
    source = st.session_state.get("album_input_row")
    artist, album = "", ""
    if source:
        job, seq, artist, title, album = source
        st.write(f"입력 {seq}번 · {artist} — {title}")
    st.subheader("앨범 수록곡")

    def official_reader(playlist_id: str, release_title: str) -> None:
        def review(entries: list[AlbumTrack]) -> None:
            # 입력 행의 앨범명과 카탈로그에서 선택한 실제 발매본 이름을 구분한다.
            rows = [(row.video_id, row.title, row.channel, release_title) for row in entries]
            show_track_review(artist, title, album, rows, context=f"album-{job}-{seq}-{playlist_id}")

        def choose(track: AlbumTrack) -> None:
            if source:
                music_store().choose_album_track(job, seq, track.video_id, track.title, track.channel)
                st.session_state.music_next_job = job
                st.session_state.music_notice = f"입력 {seq}번에 {track.title} 곡을 확정했습니다."
                go_to("음원 목록")
            else:
                open_video_job(f"album:{playlist_id}", track.title, [(track.video_id, track.title, track.channel)])

        video.album_reader(video_store(), playlist_id, on_choose=choose, on_review=review if source else None,
                           choose_label="이 수록곡을 입력 행에 확정" if source else "선택한 수록곡으로 영상 작업 만들기")

    album_search(artist, False, on_release=official_reader, initial_album=album)


def main() -> None:
    st.set_page_config(page_title="재생목록 작업실", layout="wide")
    st.title("재생목록 작업실")
    if next_view := st.session_state.pop("next_workspace_view", None):
        st.session_state.workspace_view = next_view
    view = st.radio("작업 화면", ["영상 목록", "음원 목록", "앨범 수록곡"], horizontal=True,
                    key="workspace_view", on_change=clear_album_context)
    if view == "영상 목록":
        video.workspace()
    elif view == "음원 목록":
        music.workspace(on_lookup=lookup_input, on_prepare=prepare_music_job)
    else:
        album_workspace()


if __name__ == "__main__":
    try:
        main()
    except Timeout:
        st.warning("다른 창에서 작업을 처리하고 있습니다. 완료 후 다시 시도하세요.")
    except (video.AuthError, video.PlaylistError, music.auth.AuthError, music.matcher.SearchError,
            music.playlist.PlaylistError, ValueError) as exc:
        st.error(str(exc))
    except (sqlite3.Error, OSError):
        st.error("로컬 작업을 열거나 저장하지 못했습니다. 폴더 권한과 남은 공간을 확인하세요.")
