"""입력 파일의 행과 열을 읽고 검색어 또는 영상 ID를 만든다."""

from __future__ import annotations

import re
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pandas as pd


def video_id(value: str) -> str | None:
    """공식 유튜브 URL에서 정확히 11자 영상 ID만 추출한다."""
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
    except ValueError:
        return None
    if url.scheme not in {"https", "http"}:
        return None
    path = url.path.strip("/").split("/")
    if host in {"youtu.be", "www.youtu.be"} and len(path) == 1:
        candidate = path[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if path == ["watch"]:
            candidate = parse_qs(url.query).get("v", [""])[0]
        elif len(path) == 2 and path[0] in {"shorts", "embed", "live"}:
            candidate = path[1]
        else:
            return None
    else:
        return None
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None


def decode_text(data: bytes) -> str:
    for codec in ("utf-8-sig", "cp949"):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    raise ValueError("UTF-8 또는 CP949 인코딩으로 다시 저장한 파일을 올려 주세요.")


def table_cells(line: str) -> list[str]:
    # 이스케이프된 세로선은 셀 안의 문자로 유지한다.
    content = line.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|") and not content.endswith(r"\|"):
        content = content[:-1]
    return [re.sub(r"\\([\\|`\[\]])", r"\1", cell.strip())
            for cell in re.split(r"(?<!\\)\|", content)]


def markdown_table(text: str) -> pd.DataFrame:
    lines = text.splitlines()
    rows: list[list[str]] = []
    header: list[str] = []
    for index, line in enumerate(lines):
        cells = table_cells(line)
        if index and "|" in line and cells and all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            header = table_cells(lines[index - 1])
            if not all(header) or len(header) != len(cells) or len(set(header)) != len(header):
                raise ValueError("표의 각 열에는 서로 다른 이름이 필요합니다.")
            for row in lines[index + 1:]:
                if not row.strip():
                    continue
                if "|" not in row:
                    break
                values = table_cells(row)
                if len(values) != len(header):
                    raise ValueError("마크다운 표에서 열 개수가 다른 행을 확인해 주세요.")
                rows.append(values)
            break
    if not header:
        raise ValueError("머리글 바로 아래에 | --- | 구분선이 있는 표를 올려 주세요.")
    return pd.DataFrame(rows, columns=header)


def read_file(name: str, data: bytes) -> pd.DataFrame:
    suffix = Path(name).suffix.lower()
    if suffix == ".xlsx":
        frame = pd.read_excel(BytesIO(data), dtype=str, keep_default_na=False, engine="openpyxl")
    elif suffix == ".csv":
        frame = pd.read_csv(StringIO(decode_text(data)), dtype=str, keep_default_na=False)
    elif suffix == ".md":
        frame = markdown_table(decode_text(data))
    elif suffix == ".txt":
        frame = pd.DataFrame({"검색어": decode_text(data).splitlines()})
    else:
        raise ValueError("MD, CSV, XLSX, TXT 파일을 선택해 주세요.")
    frame.columns = frame.columns.map(str)
    frame = frame.fillna("").astype(str).apply(lambda col: col.str.strip())
    frame = frame.loc[frame.ne("").any(axis=1)].reset_index(drop=True)
    if frame.empty:
        raise ValueError("입력 항목이 없습니다. 파일에 한 줄 이상 추가해 주세요.")
    return frame


def build_inputs(frame: pd.DataFrame, columns: list[str]) -> list[tuple[str, str | None]]:
    """선택한 열 중에 영상 URL이 있으면 검색을 건너뛴다."""
    items: list[tuple[str, str | None]] = []
    for cells in frame[columns].itertuples(index=False, name=None):
        values = [value for value in cells if value]
        query = " ".join(values)
        if query:
            direct = next((found for value in values if (found := video_id(value))), None)
            items.append((query, direct))
    return items
