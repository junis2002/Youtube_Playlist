"""Ollama의 로컬 모델 확인, 곡 정보 전송 시점과 추천 응답 검증을 시험한다."""

import os
import unittest
from unittest.mock import patch, sentinel

from core import local_model


class ModelFixture:
    local = "small:latest"
    alias = "my-alias:latest"

    def __init__(self) -> None:
        self.tags = [self.local, self.alias, "embedded:latest", "other-alias:latest",
                     "large:480b-cloud", "remote:cloud", "private-cloud:latest"]
        self.local_info = {"details": {"format": "gguf"}, "model_info": {"general.architecture": "llama"},
                           "capabilities": ["completion"]}
        self.shows = {
            self.local: self.local_info,
            self.alias: dict(self.local_info, remote_model="large:latest"),
            "other-alias:latest": dict(self.local_info, remote_host="https://remote.invalid"),
            "embedded:latest": dict(self.local_info, capabilities=["embedding"]),
        }
        self.reply = {"model": self.local, "done": True, "message": {"role": "assistant", "content": "검토 응답"}}
        self.calls: list[tuple[str, dict | None]] = []

    def request(self, path: str, payload: dict | None = None) -> dict:
        self.calls.append((path, payload))
        if path == "/api/tags":
            return {"models": [{"name": name, "model": name} for name in self.tags]}
        if path == "/api/show":
            return self.shows.get(payload["model"], self.local_info)
        if path == "/api/chat":
            return self.reply
        raise AssertionError(f"예기치 않은 엔드포인트 {path}")


class LocalModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ModelFixture()
        mock = patch.object(local_model, "_request", side_effect=self.provider.request)
        mock.start()
        self.addCleanup(mock.stop)

    def test_list_excludes_cloud_names_remote_aliases_and_embeddings(self) -> None:
        self.assertEqual(local_model.list_models(), [self.provider.local])
        self.assertFalse(any(path == "/api/chat" for path, _ in self.provider.calls))

    def test_only_an_installed_local_model_receives_the_prompt(self) -> None:
        with (patch.object(local_model, "build_prompt", return_value="가상의 곡 자료") as prompt,
              patch.object(local_model, "parse_reply", return_value=sentinel.recommendation) as parse):
            result = local_model.recommend(sentinel.case, self.provider.local)

        self.assertIs(result, sentinel.recommendation)
        prompt.assert_called_once_with(sentinel.case)
        parse.assert_called_once_with(sentinel.case, "검토 응답")
        self.assertIn(("/api/show", {"model": self.provider.local}), self.provider.calls[:-1])
        path, body = self.provider.calls[-1]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["model"], self.provider.local)
        self.assertFalse(body["stream"])
        self.assertIn({"role": "user", "content": "가상의 곡 자료"}, body["messages"])

    def test_remote_alias_change_after_listing_prevents_metadata_submission(self) -> None:
        self.assertEqual(local_model.list_models(), [self.provider.local])
        self.provider.shows[self.provider.local] = dict(self.provider.local_info, remote_host="https://remote.invalid")
        self.provider.calls.clear()
        # 목록을 그린 시점의 판정만 믿으면 다음 요청에서 곡 자료가 외부로 전달될 수 있다.
        with patch.object(local_model, "build_prompt", return_value="개인 제목"):
            for name in (self.provider.local, "absent:latest", "remote:cloud", "embedded:latest"):
                with self.subTest(name=name), self.assertRaises(local_model.LocalModelError):
                    local_model.recommend(sentinel.case, name)
        self.assertTrue(all(path in {"/api/tags", "/api/show"} for path, _ in self.provider.calls))
        self.assertNotIn("개인 제목", str(self.provider.calls))

    def test_model_with_unverifiable_details_never_receives_a_chat(self) -> None:
        for info in ({}, {"details": {}}, {"details": {"format": ""}},
                     dict(self.provider.local_info, capabilities={"completion": True})):
            with self.subTest(info=info):
                self.provider.shows[self.provider.local] = info
                with self.assertRaises(local_model.LocalModelError):
                    local_model.recommend(sentinel.case, self.provider.local)
        self.assertFalse(any(path == "/api/chat" for path, _ in self.provider.calls))

    def test_invalid_assistant_reply_and_parser_errors_are_safe(self) -> None:
        with (patch.object(local_model, "build_prompt", return_value="곡"),
              patch.object(local_model, "parse_reply", side_effect=ValueError("민감한 본문")) as parse):
            self.provider.reply = {"model": self.provider.local, "done": True, "message": {"content": 42}}
            with self.assertRaises(local_model.LocalModelError):
                local_model.recommend(sentinel.case, self.provider.local)
            parse.assert_not_called()

            self.provider.reply = {"model": self.provider.local, "done": True,
                                   "message": {"role": "assistant", "content": "민감한 본문"}}
            with self.assertRaises(local_model.LocalModelError) as error:
                local_model.recommend(sentinel.case, self.provider.local)
            parse.assert_called_once_with(sentinel.case, "민감한 본문")
            self.assertNotIn("민감한 본문", str(error.exception))


class LocalHttpTests(unittest.TestCase):
    def test_fixed_loopback_connection_and_no_redirect_or_raw_body(self) -> None:
        # HTTP 연결 생성기를 가로채 실제 프록시나 Ollama에 요청이 도달하지 않게 한다.
        for status, body in ((200, b'{"models":[]}'), (302, b"private-body"),
                             (503, b"private-body"), (200, b"private-body")):
            with (self.subTest(status=status, body=body),
                  patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid", "NO_PROXY": ""}),
                  patch("http.client.HTTPConnection", autospec=True) as connect):
                connection = connect.return_value
                response = connection.getresponse.return_value
                response.status = status
                response.getheader.return_value = "http://not-local.invalid/elsewhere"
                response.read.return_value = body
                if body == b'{"models":[]}':
                    self.assertEqual(local_model.list_models(), [])
                    size = response.read.call_args.args[0]
                    self.assertGreater(size, 0)
                else:
                    with self.assertRaises(local_model.LocalModelError) as error:
                        local_model.list_models()
                    self.assertNotIn("private-body", str(error.exception))
                self.assertEqual(connect.call_count, 1)
                self.assertEqual(connect.call_args.args, ("127.0.0.1", 11434))
                self.assertGreater(connect.call_args.kwargs["timeout"], 0)
                self.assertEqual(connection.request.call_count, 1)
                self.assertEqual(connection.request.call_args.args[:2], ("GET", "/api/tags"))
                connection.close.assert_called_once_with()


    def test_oversized_response_is_rejected_even_if_it_contains_valid_json(self) -> None:
        def oversized_body(count: int) -> bytes:
            self.assertTrue(0 < count <= 8 * 1024 * 1024)
            return b" " * (count - 2) + b"{}"

        with patch("http.client.HTTPConnection", autospec=True) as connect:
            response = connect.return_value.getresponse.return_value
            response.status = 200
            response.read.side_effect = oversized_body
            with self.assertRaises(local_model.LocalModelError):
                local_model._request("/api/show", {"model": "small:latest"})
            response.read.assert_called_once()
            connect.return_value.close.assert_called_once_with()
