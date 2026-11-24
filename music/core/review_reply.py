"""앱과 오프라인 대조 도구가 공유하는 원본 모델 응답 검증 규칙."""

import json
import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass

MAX_REASON_LENGTH = 200
SNAPSHOT_ID = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Recommendation:
    video_id: str | None
    reason: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("같은 응답 필드가 두 번 사용됐습니다.")
    return result


def validate_reply(raw: str, candidates: Mapping[str, Collection[str]]) -> tuple[str, Recommendation]:
    """검토 중인 목록에 속하는 추천 또는 보류만 받는다."""
    if not isinstance(raw, str) or len(raw) > 8_192:
        raise ValueError("AI 응답은 8,192자 이내의 JSON으로 입력해 주세요.")
    body = raw.strip()
    if body.startswith("```"):
        fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", body, flags=re.IGNORECASE | re.DOTALL)
        if not fence:
            raise ValueError("JSON 코드 블록 한 개 또는 JSON 본문만 입력해 주세요.")
        body = fence[1]
    try:
        reply = json.loads(body, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise ValueError("중복 필드가 없는 올바른 JSON 객체를 입력해 주세요.") from None
    if not isinstance(reply, dict) or set(reply) != {"case_id", "video_id", "reason"}:
        raise ValueError("응답에는 case_id, video_id, reason 세 필드가 필요합니다.")
    case_id = reply["case_id"]
    if not isinstance(case_id, str) or not SNAPSHOT_ID.fullmatch(case_id) or case_id not in candidates:
        raise ValueError("다른 곡 또는 다른 후보 목록에 대한 응답입니다. 현재 검토 요청을 사용해 주세요.")
    video_id = reply["video_id"]
    if video_id is not None and (not isinstance(video_id, str) or video_id not in candidates[case_id]):
        raise ValueError("응답의 영상 ID가 현재 검토 후보에 없습니다.")
    reason = reply["reason"]
    if (not isinstance(reason, str) or not 1 <= len(reason.strip()) <= MAX_REASON_LENGTH
            or any(unicodedata.category(ch) in {"Cc", "Cs", "Zl", "Zp"} for ch in reason)
            or not re.search(r"[가-힣ㄱ-ㅎㅏ-ㅣᄀ-ᇿ]", reason)):
        raise ValueError(f"추천 또는 보류 근거를 한국어 한 줄로 {MAX_REASON_LENGTH}자 이내에 입력해 주세요.")
    return case_id, Recommendation(video_id, reason.strip())
