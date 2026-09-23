"""Small text helpers for FEAT-D1."""
from __future__ import annotations

import re


def slugify(text: str) -> str:
    """Lowercase `text` and collapse every run of non-alphanumerics into one hyphen.

    No leading or trailing hyphen is produced, so the result is safe to use as a
    document anchor.
    """
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
