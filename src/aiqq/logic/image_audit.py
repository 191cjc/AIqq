"""Account exemptions for drawing audits and local unaudited layout selection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ImageOrientation


@dataclass(frozen=True)
class ImageAuditPolicy:
    exempt_member_ids: frozenset[str] = frozenset()

    def is_exempt(self, member_openid: str) -> bool:
        return bool(member_openid) and member_openid in self.exempt_member_ids


def infer_image_orientation(prompt: str) -> ImageOrientation:
    """Recognize explicit pose/layout hints without a model or safety audit."""
    normalized = prompt.casefold().replace("_", " ")
    if re.search(r"\b(?:lying|reclining)\b", normalized) or any(
        hint in normalized for hint in ("躺", "卧姿", "平卧", "侧卧", "仰卧", "俯卧")
    ):
        return "landscape"
    if re.search(r"\bfull\s+body\b", normalized) or "全身" in normalized:
        return "portrait"
    return "square"
