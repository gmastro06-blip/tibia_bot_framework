from __future__ import annotations

import os
import re
from typing import Optional, Tuple


def parse_current_and_max_with_reason(
    text: str,
    *,
    label: str = "",
    max_value: int | None = None,
    allow_single_number: bool = True,
) -> Tuple[Optional[int], Optional[int], str]:
    """Parse `current/max` style HUD strings.

    Accepts separators `/` or `|` (common OCR ambiguity).

    Returns:
      (current, max, reason)

    Reasons:
      - ok
      - invalid_range
      - single_number
      - parse_fail
      - exception
    """

    try:
        raw = str(text or "")
        cleaned = re.sub(r"[^0-9/|\s]", "", raw)
        cleaned = cleaned.replace("|", "/")

        if max_value is None:
            try:
                max_value = int(float(os.getenv("HPMP_MAX_OCR", "100000").strip() or "100000"))
            except Exception:
                max_value = 100000
        max_value = int(max(1000, max_value))

        # current/max
        m = re.search(r"(\d{1,6})\s*/\s*(\d{1,6})", cleaned)
        if m:
            cur = int(m.group(1))
            mx = int(m.group(2))
            if mx > 0 and 0 <= cur <= mx <= int(max_value):
                return cur, mx, "ok"
            return None, None, "invalid_range"

        # Fallback: single number (e.g. OCR misses the slash)
        if allow_single_number:
            nums = re.findall(r"\d{1,6}", cleaned)
            if nums:
                for s in nums:
                    v = int(s)
                    if 0 <= v <= int(max_value):
                        return v, None, "single_number"

        if label:
            _ = label  # kept for API parity; no logging here
        return None, None, "parse_fail"
    except Exception:
        return None, None, "exception"
