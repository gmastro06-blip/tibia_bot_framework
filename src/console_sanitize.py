from __future__ import annotations

import os
import sys
import unicodedata
from typing import TextIO


# When running in some Windows terminals/log pipelines (notably with Tee-Object
# or when viewing logs in non-UTF8 editors), emoji characters can show up as
# mojibake like "ƒÄ«  ÔÅ▒´©Å".
#
# Set NO_EMOJI=1 to replace/strip emojis from stdout/stderr.


_TRANSLATIONS: dict[str, str] = {
    "▶": ">",
    "✅": "[OK]",
    "❌": "[ERROR]",
    "🚀": "[START]",
    "🛑": "[STOP]",
    "🖥": "[MONITOR] ",
    "🖥️": "[MONITOR] ",
    "📸": "[CAPTURE] ",
    "👁": "[VISION] ",
    "👁️": "[VISION] ",
    "🧠": "[DECISION] ",
    "🩹": "[HEAL] ",
    "🧭": "[CAVEBOT] ",
    "🎮": "[STATE] ",
    "🎯": "[TARGET] ",
    "🧱": "[BLOCK] ",
    "⏱": "[TIME] ",
    "⏱️": "[TIME] ",
    "\uFE0F": "",  # Variation Selector-16
    "\u200D": "",  # Zero width joiner
}


def _sanitize_text(s: str) -> str:
    if not s:
        return s

    # Fast path: apply known translations first.
    for k, v in _TRANSLATIONS.items():
        if k in s:
            s = s.replace(k, v)

    out_chars: list[str] = []
    for ch in s:
        o = ord(ch)

        # Remove common emoji glue.
        if ch in ("\uFE0F", "\u200D"):
            continue

        # Strip most emoji blocks (supplementary planes).
        if o >= 0x1F000:
            continue

        # Strip miscellaneous symbol emojis (older block) without touching
        # accented letters or common punctuation.
        try:
            if unicodedata.category(ch) == "So" and o >= 0x2600:
                continue
        except Exception:
            pass

        out_chars.append(ch)

    s2 = "".join(out_chars)

    # PowerShell + Tee-Object often ends up interpreting UTF-8 bytes as the
    # current OEM code page (e.g. CP437), which corrupts accented letters.
    # Since NO_EMOJI is already an opt-in “sanitize logs” mode, we also
    # normalize to ASCII here for maximum robustness.
    try:
        # Decompose accents (NFKD), then drop combining marks.
        norm = unicodedata.normalize("NFKD", s2)
        norm = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
        # Finally drop any remaining non-ASCII characters.
        norm = norm.encode("ascii", errors="ignore").decode("ascii")
        return norm
    except Exception:
        return s2


class _SanitizedWriter:
    def __init__(self, wrapped: TextIO) -> None:
        self._wrapped = wrapped

    def write(self, s: str) -> int:  # type: ignore[override]
        try:
            return self._wrapped.write(_sanitize_text(str(s)))
        except Exception:
            # Best-effort: never break logging.
            return 0

    def flush(self) -> None:
        try:
            self._wrapped.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._wrapped.isatty())
        except Exception:
            return False

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)


def no_emoji_enabled() -> bool:
    raw = os.getenv("NO_EMOJI", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def maybe_install_no_emoji_output() -> bool:
    """If NO_EMOJI=1, wrap sys.stdout/sys.stderr to strip emojis.

    Returns True if the sanitizer was installed.
    """

    if not no_emoji_enabled():
        return False

    try:
        if not isinstance(sys.stdout, _SanitizedWriter):
            sys.stdout = _SanitizedWriter(sys.stdout)  # type: ignore[assignment]
        if not isinstance(sys.stderr, _SanitizedWriter):
            sys.stderr = _SanitizedWriter(sys.stderr)  # type: ignore[assignment]
        return True
    except Exception:
        return False
