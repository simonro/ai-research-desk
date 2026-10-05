"""The Obsidian note: the condensed report, with the vault's frontmatter.

The vault is the qualitative layer, so what goes there is the one-page read, not
the full evidence dump. The frontmatter follows the schema the dashboards query
(`type`, `bucket`, `status`, `created`, `updated`, `tags`, plus the thesis
fields), so a note written here appears in the same views as one written by
hand.

Each ticker keeps ONE note, rewritten each run, with a short history table at
the bottom. A new file per week would bury the vault in near-identical notes and
make "what did I think about this in September" unanswerable.
"""

from __future__ import annotations

from edgedesk.verdict.published import published
import os
import re
from datetime import date
from pathlib import Path

from edgedesk.reports import condensed
from edgedesk.reports.common import num, pct, price

def folder() -> Path | None:
    """Where the notes go: EDGE_DESK_VAULT_DIR, a folder inside a markdown vault such as Obsidian.
    Unset means no notes. Read when needed, so a value from ~/.edge-desk/.env counts."""
    value = os.environ.get("EDGE_DESK_VAULT_DIR")
    return Path(value) if value else None

_HISTORY_START = "<!-- engine:history -->"
_HISTORY_END = "<!-- /engine:history -->"


def note_path(ticker: str, name: str | None = None) -> Path:
    """`TICKER - Company.md`, matching the vault's position-thesis convention."""
    stem = f"{ticker.upper()} - {name}" if name else ticker.upper()
    return (folder() or Path(".")) / f"{_safe(stem)}.md"


def _safe(stem: str) -> str:
    # A trailing period gives "Broadcom Inc..md", which Windows also dislikes.
    return re.sub(r'[<>:"/\\|?*]', "", stem).strip().rstrip(". ")


def frontmatter(run: dict) -> str:
    ev = run.get("evidence") or {}
    lt = (run.get("verdicts") or {}).get("long_term") or {}
    levels = ((run.get("levels") or {}).get("long_term") or {})
    fv = ((run.get("valuation") or {}).get("fair_value")) or {}
    risk = (run.get("factors") or {}).get("risk") or {}
    today = date.today().isoformat()

    lines = [
        "---",
        "type: thesis",
        "bucket: investing",
        "status: active",
        f"created: {run['as_of']}",
        f"updated: {today}",
        f"ticker: {run['ticker']}",
        f"conviction: {lt.get('conviction') or 'None'}",
        f"rating_long_term: {lt.get('rating') or 'withheld'}",
        f"swing_call: {(run.get('swing') or {}).get('call') or 'no call'}",
        f"target: {fv.get('value') if fv else ''}",
        f"risk: {num(risk.get('score'), 0) if risk.get('score') is not None else ''}",
        f"accumulate_below: {(levels.get('accumulation_zone') or {}).get('price') or ''}",
        f"quality_state: {lt.get('quality_state') or ''}",
        f"data_quality: {ev.get('quality_score')}",
        f"engine_run: {run.get('content_hash')}",
        "tags: [investing, engine, research]",
        "---",
        "",
    ]
    return "\n".join(lines)


def history_row(run: dict) -> str:
    lt = published(run, "long_term")
    close = (run.get("evidence") or {}).get("anchors", {}).get("last_close")
    fv = ((run.get("valuation") or {}).get("fair_value")) or {}
    return (f"| {run['as_of']} | {price(close)} | {(run.get('swing') or {}).get('call') or 'no call'} | "
            f"{lt.get('rating') or 'withheld'} | "
            f"{price(fv.get('value')) if fv else 'n/a'} | "
            f"`{run.get('content_hash')}` |")


def render(run: dict, previous_history: list[str] | None = None) -> str:
    """The full note: frontmatter, the condensed report, then the run history."""
    rows = list(previous_history or [])
    row = history_row(run)
    stamp = run["as_of"]
    rows = [r for r in rows if not r.startswith(f"| {stamp} |")]
    rows.insert(0, row)

    body = condensed.render(run)
    # The note's own H1 comes from the filename in Obsidian, so the report's H1
    # is demoted to keep one title per note.
    body = re.sub(r"^# ", "## ", body, count=1)

    return "\n".join([
        frontmatter(run),
        body,
        "",
        "## Run history",
        "",
        _HISTORY_START,
        "| Date | Close | Swing | Long term | Fair value | Run |",
        "|---|---:|---|---|---:|---|",
        *rows,
        _HISTORY_END,
        "",
    ])


def existing_history(path: Path) -> list[str]:
    """The history rows already in the note, so a rewrite does not lose them."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if _HISTORY_START not in text or _HISTORY_END not in text:
        return []
    block = text.split(_HISTORY_START, 1)[1].split(_HISTORY_END, 1)[0]
    return [line.strip() for line in block.splitlines()
            if line.strip().startswith("| 2") or line.strip().startswith("| 1")]


def write(run: dict, folder: Path | str | None = None) -> str | None:
    """Write or update the ticker's note. Returns None when there is no vault."""
    base = Path(folder) if folder else globals()["folder"]()
    if base is None or not base.parent.exists():
        # No Obsidian vault on this machine: not an error, just nothing to do.
        return None
    name = ((run.get("evidence") or {}).get("profile") or {}).get("name")
    path = base / note_path(run["ticker"], name).name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(run, existing_history(path)), encoding="utf-8")
    return str(path)
