"""네 가지 음원 입력 포맷과 한글 인코딩 회귀 테스트."""

from io import BytesIO
import unittest

import pandas as pd

from core.parser import guess_columns, map_tracks, parse_file


class ParserTests(unittest.TestCase):
    def test_markdown_escapes_and_alignment(self) -> None:
        data = r"""
# 나의 앨범
| 가수 | 앨범 | 곡명 | 디스크 | 트랙 |
| :--- | --- | ---: | --- | --- |
| 가수 A | \[봄\] | \`여름\` \| 가을 | 1 | 5 |

| 가수 B | 밤 | 새벽 | 2 | 3 |
"""
        parsed = parse_file("앨범.md", data.encode())
        tracks = map_tracks(parsed, *guess_columns(list(parsed.columns)))
        self.assertEqual(tuple(tracks.iloc[0]), ("가수 A", "`여름` | 가을", "[봄]"))
        self.assertEqual(len(tracks), 2)

    def test_csv_korean_and_excel(self) -> None:
        frame = pd.DataFrame({"Artist": ["가수", "빈 항목"], "Title": ["노래, 1", ""], "Album": ["봄", ""]})
        workbook = BytesIO()
        frame.to_excel(workbook, index=False, engine="openpyxl")
        for name, data in [("노래.csv", frame.to_csv(index=False).encode("cp949")), ("노래.xlsx", workbook.getvalue())]:
            with self.subTest(name=name):
                parsed = parse_file(name, data)
                tracks = map_tracks(parsed, *guess_columns(list(parsed.columns)))
                self.assertEqual(list(tracks.itertuples(index=False, name=None)), [("가수", "노래, 1", "봄")])

    def test_text_line_numbers(self) -> None:
        self.assertEqual(parse_file("곡.txt", "가수 - 노래 - 봄".encode()).iloc[0]["곡명"], "노래 - 봄")
        with self.assertRaisesRegex(ValueError, "2번째"):
            parse_file("곡.txt", "가수 - 노래\n가수만".encode())
