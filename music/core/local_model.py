"""PC에 설치된 Ollama 모델로 후보를 비교하고 기존 결과 검증기를 거친다."""

from __future__ import annotations

import http.client
import json
from typing import Any, Literal

from .ai_review import Recommendation, ReviewCase, build_prompt, parse_reply


class LocalModelError(RuntimeError):
    """화면에 표시할 수 있는 로컬 모델 오류."""


def _request(path: Literal["/api/tags", "/api/show", "/api/chat"], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    # HTTPConnection은 시스템 프록시를 참조하지 않고 3xx를 따라가지 않는다.
    chat = path == "/api/chat"
    connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=120 if chat else 8)
    limit = 128 * 1024 if chat else 4 * 1024 * 1024
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request("POST" if data is not None else "GET", path, body=data,
                           headers={"Content-Type": "application/json", "Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise LocalModelError(f"이 PC의 Ollama 요청이 실패했습니다(HTTP {response.status}). 설치한 모델을 확인하세요.")
        body = response.read(limit + 1)
        if len(body) > limit:
            raise LocalModelError("로컬 모델 응답이 너무 큽니다. 다른 모델로 다시 시도하세요.")
        result = json.loads(body.decode("utf-8"))
    except (OSError, http.client.HTTPException):
        raise LocalModelError("이 PC의 Ollama 응답을 받지 못했습니다. Ollama 실행 상태와 모델을 확인하세요.") from None
    except (ValueError, RecursionError):
        raise LocalModelError("이 PC의 Ollama가 올바른 JSON 응답을 반환하지 않았습니다.") from None
    finally:
        connection.close()
    if not isinstance(result, dict) or "error" in result:
        raise LocalModelError("이 PC의 Ollama가 요청을 완료하지 못했습니다. 설치한 모델을 확인하세요.")
    return result


def _cloud_tag(name: str) -> bool:
    # 모델명 및 태그 양쪽에 쓰이는 Ollama 클라우드 접미사를 확인한다.
    parts = name.strip().rsplit("/", 1)[-1].casefold().split(":")
    return any(part == "cloud" or part.endswith("-cloud") for part in parts)


def _remote_model(info: dict[str, Any]) -> bool:
    for section in (info, info.get("details"), info.get("model_info")):
        if not isinstance(section, dict):
            continue
        if any(section.get(key) for key in ("remote_model", "remote_host")):
            return True
        if any(isinstance(section.get(key), str) and _cloud_tag(section[key])
               for key in ("name", "model", "parent_model")):
            return True
    return False


def _installed() -> list[str]:
    rows = _request("/api/tags").get("models")
    if not isinstance(rows, list):
        raise LocalModelError("Ollama에서 설치된 모델 목록을 읽을 수 없습니다.")
    names: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"].strip():
            raise LocalModelError("Ollama의 설치 모델 정보가 올바르지 않습니다.")
        if not _remote_model(row):
            names.append(row["name"])
    return list(dict.fromkeys(names))


def _local_chat_model(name: str) -> bool:
    info = _request("/api/show", {"model": name})
    if _remote_model(info):
        return False
    # 구버전 Ollama에는 capabilities가 없을 수 있다. 로컬 모델 형식은 별도로 확인한다.
    capabilities = info.get("capabilities")
    if capabilities is not None and (not isinstance(capabilities, list) or "completion" not in capabilities):
        return False
    details = info.get("details")
    model_format = details.get("format") if isinstance(details, dict) else None
    return isinstance(model_format, str) and model_format.strip().casefold() not in {"", "cloud", "remote"}


def list_models() -> list[str]:
    """설치 목록에서 실제 로컬 모델 정보와 채팅 지원 여부를 확인한다."""
    return [name for name in _installed() if _local_chat_model(name)]


def recommend(case: ReviewCase, model: str) -> Recommendation:
    """선택한 모델을 재확인한 뒤 현재 행과 후보를 비교해 추천을 돌려준다."""
    if not isinstance(model, str) or _cloud_tag(model) or model not in _installed() or not _local_chat_model(model):
        raise LocalModelError("설치된 로컬 채팅 모델을 다시 선택하세요.")
    try:
        prompt = build_prompt(case)
    except ValueError:
        raise LocalModelError("추천 요청을 만들 수 없습니다. 입력 행과 후보를 다시 확인하세요.") from None
    response = _request("/api/chat", {
        "model": model, "stream": False, "format": "json",
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "num_predict": 768},
    })
    message = response.get("message")
    if (response.get("done") is not True or response.get("model", model) != model
            or not isinstance(message, dict) or message.get("role") != "assistant"
            or not isinstance(message.get("content"), str)):
        raise LocalModelError("로컬 모델의 답변이 완성되지 않았습니다. 다시 시도하세요.")
    try:
        return parse_reply(case, message["content"])
    except ValueError:
        raise LocalModelError("로컬 모델의 추천이 현재 후보 또는 응답 형식과 맞지 않습니다. 후보를 직접 검토하세요.") from None
