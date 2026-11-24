"""공식 API 할당량 없이 후보 5개를 검색하고 조건에 맞춰 정렬한다."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz
from yt_dlp import YoutubeDL


@dataclass(frozen=True)
class Filters:
    exclude_short: bool = False
    min_minutes: float = 0
    max_minutes: float = 0
    priority: str = "유사도"
    channel: str = ""
    exclude: str = ""


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def search(query: str, filters: Filters) -> list[dict[str, Any]]:
    # 상세 미디어를 다운로드하지 않고 검색 목록의 메타데이터만 사용한다.
    options = {"quiet": True, "no_warnings": True, "skip_download": True,
               "extract_flat": True, "socket_timeout": 15, "retries": 2,
               "extractor_retries": 2, "noplaylist": True}
    with YoutubeDL(options) as client:
        result = client.extract_info("ytsearch5:" + query, download=False)
    if result is None:
        raise RuntimeError("검색 결과를 가져오지 못했습니다.")
    return rank(query, result.get("entries") or [], filters)


def rank(query: str, entries: list[dict[str, Any]], filters: Filters) -> list[dict[str, Any]]:
    excluded = [normalized(k) for k in filters.exclude.split(",") if k.strip()]
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        if not entry:
            continue
        vid = entry.get("id", "")
        if not re.fullmatch(r"[\w-]{11}", vid or "", re.ASCII):
            continue
        title = str(entry.get("title") or "")
        channel = str(entry.get("channel") or entry.get("uploader") or "")
        duration = entry.get("duration")
        if duration is None and (filters.exclude_short or filters.min_minutes or filters.max_minutes):
            continue
        if duration is not None and (duration < filters.min_minutes * 60
                                     or (filters.exclude_short and duration <= 60)
                                     or (filters.max_minutes and duration > filters.max_minutes * 60)):
            continue
        source = normalized(f"{channel} {title}")
        if any(word in source for word in excluded):
            continue
        score = round(max(fuzz.ratio(normalized(query), normalized(title)),
                          fuzz.token_sort_ratio(normalized(query), source)), 1)
        candidates.append({"id": vid, "title": title, "channel": channel, "duration": duration,
                           "view_count": entry.get("view_count"), "score": score,
                           "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                           "webpage_url": f"https://www.youtube.com/watch?v={vid}"})
    def priority(item: dict[str, Any]) -> tuple[float, float]:
        score = item["score"]
        if filters.priority == "조회수":
            return item.get("view_count") or 0, score
        if filters.priority == "채널명 포함" and filters.channel.strip():
            return int(normalized(filters.channel) in normalized(item["channel"])), score
        return score, item.get("view_count") or 0

    return sorted(candidates, key=priority, reverse=True)[:5]
