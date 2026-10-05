"""One line that says what an agent concluded, cut from its own text (no model call).

Used by both engine runners for the live event stream and by the dashboard for finished runs,
so a finding reads the same live and afterwards. Rules:
  - headings, table rows, rules and label-only lines are skipped;
  - each line is split into sentences on its own, so two lines never merge into one "sentence";
  - "vs.", "e.g.", "Inc." and similar do not end a sentence;
  - the pick is the first real sentence that carries a number, else the first real sentence;
  - the sentence is returned whole. It is never cut mid-way.
"""

from __future__ import annotations

import re

_ABBREV = r"(?<!\bvs)(?<!\bVs)(?<!\be\.g)(?<!\bi\.e)(?<!\bInc)(?<!\bCorp)(?<!\bCo)(?<!\bLtd)" \
          r"(?<!\bapprox)(?<!\bU\.S)(?<!\bNo)(?<!\bSt)(?<!\bMr)(?<!\bDr)(?<!\best)(?<!\bFig)"
_SPLIT = re.compile(_ABBREV + r"(?<=[.!?])\s+(?=[A-Z$(\"'])")
_LABEL = re.compile(r"^\*{1,2}[^*]{1,60}\*{1,2}:?\s*")


def sentences(text: str) -> list[str]:
    out = []
    for raw in (text or "").replace("�", "-").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "|", "---", "***", ">")):
            continue
        line = re.sub(r"^([-*+]|\d+[.)])\s+", "", line)
        line = _LABEL.sub("", line)
        line = line.replace("**", "").replace("`", "")
        line = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"\1", line)     # *italic* -> italic
        if len(line) < 140 and not re.search(r"[.!?]$", line):       # a heading or a label line
            continue
        out.extend(p.strip() for p in _SPLIT.split(line) if p.strip())
    return out


def gist(text: str, max_len: int = 420) -> str:
    parts = sentences(text)
    if not parts:
        return " ".join((text or "").split())[:max_len]
    good = [p for p in parts if 50 <= len(p) <= max_len] or parts
    return next((p for p in good if re.search(r"\d", p)), good[0])
