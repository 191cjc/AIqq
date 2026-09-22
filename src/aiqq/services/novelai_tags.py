"""Bounded, read-only Danbooru tag lookup through the shared NovelAI MCP client."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, cast

from aiqq.exceptions import NovelAITagSearchUnavailable
from aiqq.logic.models import DanbooruTag
from aiqq.services.images.novelai import NovelAIMCPTransport
from aiqq.services.novelai_tag_query import normalize_novelai_tag_query


logger = logging.getLogger(__name__)
TAG_SEARCH_TIMEOUT_SECONDS = 10
MAX_TAG_RESULTS = 100
MAX_TEXT_RESPONSE_CHARACTERS = 65_536
_CONTENT_CATEGORIES = frozenset({"general", "character", "copyright"})
_KNOWN_CATEGORIES = _CONTENT_CATEGORIES | {"artist", "meta"}


class NovelAITagSearchService:
    def __init__(self, client: NovelAIMCPTransport | None) -> None:
        self._client = client
        self._semaphore = asyncio.Semaphore(3)

    async def search(
        self, query: str, *, limit: int = 8
    ) -> tuple[DanbooruTag, ...]:
        try:
            normalized = normalize_novelai_tag_query(query)
            if type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError("Tag search limit is invalid")
            if self._client is None:
                raise ValueError("Tag search is not configured")
            async with self._semaphore:
                async with asyncio.timeout(TAG_SEARCH_TIMEOUT_SECONDS):
                    response = await self._client.call_tool(
                        "suggest_tags", {"query": normalized, "limit": limit}
                    )
            return _parse_response(response, limit=limit)
        except Exception as exc:
            logger.warning(
                "event=novelai_tag_search_failed error_type=%s", type(exc).__name__
            )
            raise NovelAITagSearchUnavailable("Danbooru tag lookup failed") from exc


def _parse_response(response: Any, *, limit: int) -> tuple[DanbooruTag, ...]:
    if not isinstance(response, dict):
        raise ValueError("Tag search response must be an object")
    _reject_error(response)
    if "structuredContent" in response:
        structured = response["structuredContent"]
        if not isinstance(structured, dict) or "result" not in structured:
            raise ValueError("Tag search structured response is invalid")
        _reject_error(structured)
        records = _record_list(structured["result"])
    else:
        records = _text_records(response.get("content"))

    candidates: dict[str, DanbooruTag] = {}
    for record in records:
        candidate = _parse_tag(record)
        if candidate is None:
            continue
        previous = candidates.get(candidate.tag)
        if previous is None or previous.post_count < candidate.post_count:
            candidates[candidate.tag] = candidate
    return tuple(
        sorted(candidates.values(), key=lambda item: (-item.post_count, item.tag))[:limit]
    )


def _reject_error(value: dict[str, Any]) -> None:
    if "error" in value or (
        "isError" in value and value["isError"] is not False
    ):
        raise ValueError("Tag search returned an error")


def _record_list(value: Any) -> list[Any]:
    if not isinstance(value, list) or len(value) > MAX_TAG_RESULTS:
        raise ValueError("Tag search result list is invalid")
    return value


def _text_records(content: Any) -> list[Any]:
    if not isinstance(content, list) or not 1 <= len(content) <= MAX_TAG_RESULTS:
        raise ValueError("Tag search text content is invalid")
    records: list[Any] = []
    text_length = 0
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            raise ValueError("Tag search content must be text")
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError("Tag search text is invalid")
        text_length += len(text)
        if text_length > MAX_TEXT_RESPONSE_CHARACTERS:
            raise ValueError("Tag search text is too large")
        decoded = json.loads(text)
        if isinstance(decoded, dict):
            _reject_error(decoded)
            entries = _record_list(decoded["result"]) if "result" in decoded else [decoded]
        else:
            entries = _record_list(decoded)
        records.extend(entries)
        if len(records) > MAX_TAG_RESULTS:
            raise ValueError("Tag search returned too many records")
    return records


def _parse_tag(record: Any) -> DanbooruTag | None:
    if not isinstance(record, dict):
        raise ValueError("Tag search record must be an object")
    _reject_error(record)
    tag = record.get("tag")
    category = record.get("category")
    count = record.get("post_count")
    if (
        not isinstance(tag, str)
        or not 1 <= len(tag) <= 120
        or not tag.isascii()
        or not tag.isprintable()
        or tag != tag.strip()
        or "," in tag
    ):
        raise ValueError("Tag search tag name is invalid")
    if not isinstance(category, str) or category not in _KNOWN_CATEGORIES:
        raise ValueError("Tag search category is invalid")
    if type(count) is not int or count < 0:
        raise ValueError("Tag search post count is invalid")
    if category not in _CONTENT_CATEGORIES:
        return None
    return DanbooruTag(
        tag=tag,
        category=cast(Literal["general", "character", "copyright"], category),
        post_count=count,
    )
