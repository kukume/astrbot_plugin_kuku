from __future__ import annotations

import re


def extract(text: str, start: str, end: str) -> str | None:
    pattern = re.escape(start) + r"(.*?)" + re.escape(end)
    match = re.search(pattern, text, re.S)
    return match.group(1) if match else None
