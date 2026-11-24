"""YouTube Music 곡 검색, 버전 구분, 후보 점수 계산."""

import re
import time
import unicodedata
from collections.abc import Callable
from typing import Any, TypeVar, TypedDict

from rapidfuzz import fuzz
from requests.exceptions import ConnectionError, HTTPError, Timeout
from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicServerError


class Candidate(TypedDict):
    videoId: str
    title: str
    artist: str
    score: float


class SearchError(RuntimeError):
    """재개 가능한 검색 오류. 계정이나 HTTP 응답의 내용을 저장하지 않는다."""


_T = TypeVar("_T")
_HIGH = 85
_INST = re.compile(r"(?<![a-z])(?:inst(?:rumental)?\.?)(?![a-z])|인스트루멘[털탈]|반주", re.I)
_EXTRA = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_VIDEO_ID = re.compile(r"[0-9A-Za-z_-]{11}")
_HTTP_CODE = re.compile(r"\bHTTP\s+(\d{3})\b", re.I)


def _normal(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


def _transient(exc: Exception) -> bool:
    if isinstance(exc, (Timeout, ConnectionError)):
        return True
    if isinstance(exc, HTTPError) and exc.response is not None:
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    if isinstance(exc, YTMusicServerError):
        # ytmusicapi는 Response 대신 메시지에 HTTP 상태 코드를 넣는다.
        match = _HTTP_CODE.search(str(exc))
        return match is not None and int(match[1]) in {408, 429, 500, 502, 503, 504}
    return False


def read_with_retry(operation: Callable[[], _T], delay: float) -> _T:
    for attempt in range(4):
        time.sleep(max(0.0, delay))
        try:
            return operation()
        except Exception as exc:
            if not _transient(exc):
                raise SearchError("검색 요청에 실패했습니다. 로그인 상태와 네트워크를 확인한 뒤 이어서 진행하세요.") from None
            if attempt == 3:
                raise SearchError("검색 요청 한도 또는 연결 오류가 계속됩니다. 잠시 후 이어서 진행하세요.") from None
            time.sleep(2.0 ** attempt)
    raise AssertionError("재시도 횟수 처리 오류")


def _score(artist: str, title: str, artists: list[str], result_title: str) -> float:
    """인스트 여부와 가수 일치도를 별도로 확인하여 동명 곡의 오등록을 줄인다."""
    expected_inst = bool(_INST.search(title))
    actual_inst = bool(_INST.search(result_title))
    left = _normal(_INST.sub("", title)) or _normal(title)
    right = _normal(_INST.sub("", result_title)) or _normal(result_title)
    title_score = fuzz.ratio(left, right)
    score = title_score
    if artist:
        target_artist = _normal(artist)
        # 합동 공연에서는 개별 참여 가수와 전체 가수 표기를 모두 비교한다.
        names = [_normal(name) for name in [*artists, " ".join(artists)] if name]
        artist_score = max((fuzz.ratio(target_artist, name) for name in names), default=0.0)
        score = 0.7 * title_score + 0.3 * artist_score
        if artist_score < 60 or title_score < 72:
            score = min(score, 79.0)
    if expected_inst != actual_inst:
        score = min(score, 60.0)
    return round(score, 1)


def search_track(
    client: YTMusic,
    artist: str,
    title: str,
    query: str | None = None,
    delay: float = 0.35,
) -> tuple[str, list[Candidate]]:
    """기본 곡 검색 후 필요하면 정제 쿼리와 동영상 검색까지 진행한다."""
    original = (query or f"{artist} {title}").strip()
    if not original:
        return "Not Found", []
    simplified = " ".join(_EXTRA.sub(" ", original).split())
    if _INST.search(title) and not _INST.search(simplified):
        simplified = f"{simplified} instrumental".strip()
    searches = [(original, "songs")]
    if simplified and simplified.casefold() != original.casefold():
        searches.append((simplified, "songs"))
    searches.append((original, "videos"))
    best: dict[str, Candidate] = {}
    inst = bool(_INST.search(title))

    def rank(candidate: Candidate) -> tuple[bool, float]:
        return (bool(_INST.search(candidate["title"])) == inst, candidate["score"])

    for phrase, kind in searches:
        results: list[dict[str, Any]] = read_with_retry(
            lambda: client.search(phrase, filter=kind, limit=6), delay
        )
        for result in results:
            video_id, found_title = str(result.get("videoId") or ""), str(result.get("title") or "")
            if not _VIDEO_ID.fullmatch(video_id) or not found_title:
                continue
            artists = [str(a["name"]) for a in result.get("artists") or [] if isinstance(a, dict) and a.get("name")]
            candidate = Candidate(
                videoId=video_id,
                title=found_title,
                artist=", ".join(artists),
                score=_score(artist, title, artists, found_title),
            )
            if video_id not in best or rank(candidate) > rank(best[video_id]):
                best[video_id] = candidate
        ordered = sorted(best.values(), key=rank, reverse=True)
        if ordered and ordered[0]["score"] >= _HIGH:
            return "High", ordered[:3]
    if not best:
        return "Not Found", []
    return "Low", sorted(best.values(), key=rank, reverse=True)[:3]
