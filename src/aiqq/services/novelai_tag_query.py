"""Shared validation for single-concept Danbooru tag queries."""

from __future__ import annotations

import re


_QUERY_PATTERN = re.compile(r"[a-z0-9][a-z0-9_().' +/!\-]*", re.ASCII)


def normalize_novelai_tag_query(query: str) -> str:
    """Preserve supported tag punctuation without allowing search operators."""
    if not isinstance(query, str) or not 1 <= len(query) <= 80:
        raise ValueError("Tag search query must contain 1–80 characters")
    if not query.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in query):
        raise ValueError("Tag search query must be printable ASCII English")
    normalized = query.strip().lower()
    if not _QUERY_PATTERN.fullmatch(normalized) or not re.search(r"[a-z]", normalized):
        raise ValueError("Tag search query must be a single concept without search operators")
    return re.sub(r" +", "_", normalized)
