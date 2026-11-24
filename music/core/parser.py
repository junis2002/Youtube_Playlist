"""업로드 파일을 읽고 선택된 열에서 음원 정보를 추출한다."""

import io
import re
from pathlib import Path

import pandas as pd


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("파일을 UTF-8 또는 CP949로 저장한 뒤 다시 올려 주세요.")


def _markdown_cells(line: str) -> list[str]:
    # 이스케이프된 세로줄은 셀 안의 글자로 보존한다.
    line = line.strip().removeprefix("|")
    if line.endswith("|") and not line.endswith(r"\|"):
        line = line[:-1]
    return [re.sub(r"\\([|\[\]`\\])", r"\1", s.strip()) for s in re.split(r"(?<!\\)\|", line)]


def _markdown(text: str) -> pd.DataFrame:
    lines = text.splitlines()
    for pos in range(1, len(lines)):
        divider = _markdown_cells(lines[pos])
        if "|" not in lines[pos] or not all(re.fullmatch(r":?-{3,}:?", s) for s in divider):
            continue
        columns = _markdown_cells(lines[pos - 1])
        if len(columns) != len(divider):
            continue
        records = []
        for number, line in enumerate(lines[pos + 1:], pos + 2):
            if not line.strip():
                continue
            if "|" not in line:
                break
            cells = _markdown_cells(line)
            if len(cells) != len(columns):
                raise ValueError(f"마크다운 {number}번째 줄의 열 수를 확인해 주세요.")
            records.append(cells)
        return pd.DataFrame(records, columns=columns)
    raise ValueError("열 이름 아래에 | --- | --- | 형식의 구분선을 넣어 주세요.")


def parse_file(name: str, data: bytes) -> pd.DataFrame:
    ext = Path(name).suffix.lower()
    if ext == ".xlsx":
        frame = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False, engine="openpyxl")
    else:
        text = decode_text(data)
        if ext == ".md":
            frame = _markdown(text)
        elif ext == ".csv":
            frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
        elif ext == ".txt":
            pairs = []
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                artist, separator, title = line.partition(" - ")
                if not separator or not artist.strip() or not title.strip():
                    raise ValueError(f"TXT {number}번째 줄을 '가수 - 곡명' 형식으로 수정해 주세요.")
                pairs.append((artist.strip(), title.strip()))
            frame = pd.DataFrame(pairs, columns=["가수", "곡명"])
        else:
            raise ValueError("MD, CSV, XLSX, TXT 중 하나를 선택해 주세요.")
    frame.columns = [str(label).strip() for label in frame.columns]
    if frame.columns.duplicated().any():
        raise ValueError("같은 열 이름이 여러 개 있습니다. 파일에서 열 이름을 구분해 주세요.")
    if frame.empty or len(frame.columns) < 2:
        raise ValueError("가수와 곡명이 들어 있는 두 개 이상의 열과 한 행 이상의 데이터가 필요합니다.")
    return frame


def guess_columns(columns: list[str]) -> tuple[str, str, str | None]:
    keys = {label.strip().casefold(): label for label in columns}

    def pick(names: tuple[str, ...]) -> str | None:
        return next((keys[k] for k in names if k in keys), None)

    artist = pick(("가수", "아티스트", "가수명", "artist", "artists", "artist name"))
    title = pick(("곡명", "제목", "노래", "곡", "노래 제목", "title", "track title", "song", "track name"))
    artist = artist or next(c for c in columns if c != title)
    title = title or next(c for c in columns if c != artist)
    return artist, title, pick(("앨범", "앨범명", "album", "album title", "album name"))


def map_tracks(frame: pd.DataFrame, artist: str, title: str, album: str | None) -> pd.DataFrame:
    # 원본 순서를 유지해 사용자가 선택하는 구간과 체크포인트 순서를 일치시킨다.
    tracks = pd.DataFrame({
        "artist": frame[artist].fillna("").astype(str).str.strip(),
        "title": frame[title].fillna("").astype(str).str.strip(),
        "album": frame[album].fillna("").astype(str).str.strip() if album else "",
    })
    return tracks[(tracks.artist != "") & (tracks.title != "")].reset_index(drop=True)
