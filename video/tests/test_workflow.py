"""일반 영상 앱의 URL 처리, 복구, API 할당량 계약을 오프라인으로 확인한다."""

import json
import unittest
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import httplib2
import pandas as pd
from googleapiclient.errors import HttpError

from core.checkpoint import Store
from core.parser import build_inputs, read_file, video_id
from core.playlist import Playlists, QuotaExceeded, quick_links
from core.searcher import Filters, rank


class VideoWorkflow(unittest.TestCase):
    def test_large_url_list_after_restart(self) -> None:
        # 1,001개 ID와 마지막 중복을 다양한 URL 형식으로 읽고 새 연결에서 복원한다.
        ids = [f"v{i:010}" for i in range(1001)]
        lines = [f"https://youtu.be/{vid}" for vid in ids]
        lines.append(f"https://www.youtube.com/shorts/{ids[0]}")
        frame = read_file("목록.txt", "\n".join(lines).encode())
        items = build_inputs(frame, ["검색어"])
        with TemporaryDirectory() as folder:
            path = Path(folder) / "한글" / "상태.sqlite3"
            job = Store(path).new_job("URL 이어하기", items, asdict(Filters()))
            restored = Store(path).items(job)
        self.assertTrue(all(row["status"] == "High" for row in restored))
        links = quick_links(row["video"] for row in restored)
        batches = [parse_qs(urlsplit(link).query)["video_ids"][0].split(",") for link in links]
        self.assertEqual([len(batch) for batch in batches], [50] * 20 + [1])
        self.assertEqual([vid for batch in batches for vid in batch], ids)

    def test_table_formats_preserve_korean_and_url(self) -> None:
        url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        columns = ["채널", "링크"]
        excel = BytesIO()
        pd.DataFrame([["자료", url]], columns=columns).to_excel(excel, index=False)
        files = {
            "파일.md": f"|채널|링크|\n|---|---|\n|자료|{url}|".encode(),
            "파일.csv": f"채널,링크\n자료,{url}".encode("cp949"),
            "파일.xlsx": excel.getvalue(),
        }
        for name, contents in files.items():
            with self.subTest(name=name):
                self.assertEqual(build_inputs(read_file(name, contents), columns),
                                 [(f"자료 {url}", "jNQXAC9IVRw")])
        self.assertIsNone(video_id("https://example.org/watch?v=jNQXAC9IVRw"))

    def test_search_filters_use_metadata(self) -> None:
        entries = [
            {"id": f"v{i:010}", "title": title, "duration": duration, "view_count": views}
            for i, (title, duration, views) in enumerate([
                ("서울 산책", 500, 15), ("서울 여행", 200, 20), ("서울 cover", 110, 25),
                ("서울", 60, 80), ("서울 생방송", None, 90)
            ])
        ]
        result = rank("서울 산책", entries, Filters(exclude_short=True, exclude="cover", priority="조회수"))
        self.assertEqual([item["title"] for item in result], ["서울 여행", "서울 산책"])
        self.assertEqual(result[1]["score"], 100)

    def test_quota_stop_and_paged_duplicate_lookup(self) -> None:
        # 실제 계정 호출 없이 Google 클라이언트의 응답 형태와 과금 단위를 재현한다.
        service = Mock()
        api_items = service.playlistItems.return_value
        req = api_items.list.return_value
        vid = "jNQXAC9IVRw"
        req.execute.side_effect = [
            {"items": [], "nextPageToken": "second"},
            {"items": [{"contentDetails": {"videoId": vid}}]},
        ]
        charged: list[int] = []
        api = Playlists(service, charged.append)
        self.assertEqual(api.existing("PL-TEST"), {vid})
        self.assertEqual(api_items.list.call_args.kwargs["pageToken"], "second")
        error = {"error": {"errors": [{"reason": "quotaExceeded"}]}}
        api_items.insert.return_value.execute.side_effect = HttpError(
            httplib2.Response({"status": "403"}), json.dumps(error).encode()
        )
        with self.assertRaises(QuotaExceeded):
            api.add("PL-TEST", vid)
        self.assertEqual(charged, [1, 1, 50])
        api_items.insert.return_value.execute.assert_called_once_with(num_retries=0)

    def test_publish_resumes_after_quota_without_recreating(self) -> None:
        from core.register import publish

        first, second = "v0000000001", "v0000000002"
        with TemporaryDirectory() as folder:
            path = Path(folder) / "체크포인트.sqlite3"
            store = Store(path)
            job = store.new_job("회복", [(first, first), (second, second)], {})
            api = Mock(spec=Playlists)
            api.create.return_value = "PL-TEST"
            api.existing.side_effect = [set(), {first}]
            api.add.side_effect = [None, QuotaExceeded("할당량"), None]
            with self.assertRaises(QuotaExceeded):
                publish(store, job, api, None, "자료", "", "private", Mock())
            self.assertEqual([r["registration"] for r in store.items(job)], ["added", "pending"])
            reopened = Store(path)
            publish(reopened, job, api, None, "자료", "", "private", Mock())
            self.assertEqual([r["registration"] for r in reopened.items(job)], ["added", "added"])
            api.create.assert_called_once()
            self.assertEqual([call.args[1] for call in api.add.call_args_list], [first, second, second])
