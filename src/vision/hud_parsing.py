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
            - no_digits
      - parse_fail
      - exception
    """

    try:
        raw = str(text or "")

        # Tolerar errores OCR comunes en HUDs (sobre todo cuando el allowlist
        # no se aplica o el modelo devuelve letras parecidas a dígitos).
        # Mantenerlo conservador: solo transformaciones obvias.
        norm = raw
        try:
            # Separadores OCR ambiguos
            norm = norm.replace("|", "/")
            # Letras que suelen representar '1'
            norm = norm.replace("I", "1").replace("l", "1").replace("!", "1")
            # Espaciado/ruido
            norm = norm.replace("\\t", " ")
        except Exception:
            norm = raw

        cleaned = re.sub(r"[^0-9/\s]", "", norm)

        # OCR sometimes returns only separators (e.g. "|" -> "/") with no digits.
        # This is not actionable and should not be treated as a parsing failure.
        try:
            if not re.search(r"\d", cleaned or ""):
                return None, None, "no_digits"
        except Exception:
            pass

        # Config de límites (fallback compatible con HPMP_MAX_OCR).
        if max_value is None:
            try:
                max_value = int(float(os.getenv("HPMP_MAX_OCR", "100000").strip() or "100000"))
            except Exception:
                max_value = 100000
        try:
            tibia_stat_max = int(float(os.getenv("TIBIA_STAT_MAX", "0").strip() or "0"))
        except Exception:
            tibia_stat_max = 0
        if tibia_stat_max and tibia_stat_max > 0:
            try:
                max_value = min(int(max_value), int(tibia_stat_max))
            except Exception:
                pass
        max_value = int(max(1000, int(max_value)))

        # Reject obviously bogus OCR maxima (e.g. "0/1") which commonly happen
        # when the ROI is misaligned or reads UI separators.
        try:
            min_max = int(float(os.getenv("HPMP_MIN_OCR_MAX", "50").strip() or "50"))
        except Exception:
            min_max = 50
        min_max = int(max(1, min_max))

        # current/max (1..7 digits)
        m = re.search(r"(\d{1,7})\s*/\s*(\d{1,7})", cleaned)
        if m:
            cur = int(m.group(1))
            mx = int(m.group(2))
            # Special case: OCR can produce "85/85" from "85|85" which is valid.
            if mx < int(min_max):
                return None, None, "invalid_range"
            if mx > 0 and 0 <= cur <= mx <= int(max_value):
                return cur, mx, "ok"
            return None, None, "invalid_range"

        # Fallback: single number (e.g. OCR misses the slash)
        if allow_single_number:
            nums = re.findall(r"\d{1,7}", cleaned)
            if nums:
                for s in nums:
                    try:
                        v = int(s)
                    except Exception:
                        continue
                    if 0 <= v <= int(max_value):
                        return v, None, "single_number"

        if label:
            _ = label  # kept for API parity; no logging here
        return None, None, "parse_fail"
    except Exception:
        return None, None, "exception"
