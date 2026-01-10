from __future__ import annotations

import re
from pathlib import Path


def test_no_os_input_injection_in_src() -> None:
    """Guardrail: fail if OS input injection libs appear in src/.

    Goal: keep the default repo in *assistant mode* (no real input injection).

    Allowlist: we currently allow the explicit input injector module:
      - src/action/movement.py

    If you want *absolute* zero-risk, set allowlist to empty and/or delete that file.
    """

    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "src"

    allow = {
        src / "action" / "movement.py",
    }

    # Patterns that indicate OS-level input injection.
    # Keep these conservative: we only want to catch clear signals.
    patterns = [
        re.compile(r"^\s*import\s+keyboard\b", re.MULTILINE),
        re.compile(r"^\s*from\s+keyboard\s+import\b", re.MULTILINE),
        re.compile(r"\bkeyboard\.", re.MULTILINE),
        re.compile(r"^\s*import\s+pyautogui\b", re.MULTILINE),
        re.compile(r"^\s*from\s+pyautogui\s+import\b", re.MULTILINE),
        re.compile(r"\bpyautogui\.", re.MULTILINE),
    ]

    offenders: list[tuple[Path, str]] = []
    for path in src.rglob("*.py"):
        if not path.is_file():
            continue
        if path in allow:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for pat in patterns:
            if pat.search(text):
                offenders.append((path, pat.pattern))
                break

    if offenders:
        details = "\n".join(f"- {p.relative_to(repo_root)} (matched: {rx})" for p, rx in offenders)
        raise AssertionError(
            "Found OS input injection references inside src/ (assistant mode guardrail).\n"
            "If intentional, add the file to the allowlist in tests/test_no_input_injection.py.\n\n"
            f"{details}"
        )
