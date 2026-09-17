"""Lossless wire records and attachment provenance shared by history queries."""

from __future__ import annotations

import json
from collections import deque
from typing import Any


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def decode_json(value: str) -> tuple[Any, str]:
    try:
        result = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None, "invalid_json"
    return result, "" if isinstance(result, dict) else "non_object"


def attachment_objects(payload: Any) -> list[tuple[str, dict[str, Any]]]:
    """Preserve each attachment's JSON pointer and object, including unknown keys.

    Breadth-first ordering gives top-level attachments first, then quoted ones.
    Do not cap this metadata scan; image download limits belong to the caller.
    """
    found: list[tuple[str, dict[str, Any]]] = []
    queue: deque[tuple[str, Any, bool]] = deque([("/d", payload, False)])
    while queue:
        path, value, is_attachment = queue.popleft()
        if isinstance(value, dict):
            if is_attachment or (
                isinstance(value.get("content_type"), str)
                and value["content_type"].lower().startswith("image/")
                and isinstance(value.get("url"), str)
            ):
                found.append((path, value))
            for key, child in value.items():
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                if key == "attachments" and isinstance(child, list):
                    queue.extend(
                        (f"{path}/{escaped}/{index}", item, True)
                        for index, item in enumerate(child)
                    )
                else:
                    queue.append((f"{path}/{escaped}", child, False))
        elif isinstance(value, list):
            queue.extend((f"{path}/{index}", item, False) for index, item in enumerate(value))
    return found


def merge_missing(old: Any, new: Any, path: str = "/d") -> tuple[Any, list[str]]:
    """Enrich duplicate creates without guessing which conflicting value is newer.

    Objects may supply missing keys. Arrays, null and other explicit values remain
    intact; a competing value is available through its immutable source version.
    """
    if isinstance(old, dict) and isinstance(new, dict):
        merged = dict(old)
        conflicts: list[str] = []
        for key, value in new.items():
            pointer = str(key).replace("~", "~0").replace("/", "~1")
            child_path = f"{path}/{pointer}"
            if key not in old:
                merged[key] = value
            else:
                merged[key], differences = merge_missing(old[key], value, child_path)
                conflicts.extend(differences)
        return merged, conflicts
    # Python considers False == 0: their JSON types are different facts.
    return old, [] if encode_json(old) == encode_json(new) else [path]
