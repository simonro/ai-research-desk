"""Report renderings. Three formats, one run file, no second analysis.

    scorecard   one screen of text: verdict, levels, factors, quality
    card        the same, as a self-contained HTML page
    condensed   one page of markdown: verdict, why, valuation, risks, what to watch
    full        everything, with the evidence table and the provenance

They agree on every number because none of them computes anything. A renderer
that needs a figure the run file does not contain is a sign the run should have
recorded it, not a licence for the renderer to work it out.
"""

from __future__ import annotations

from pathlib import Path

from edgedesk.reports import card, condensed, full, scorecard

FORMATS = {
    "scorecard": (scorecard.render, "txt"),
    "card": (card.render, "html"),
    "condensed": (condensed.render, "md"),
    "full": (full.render, "md"),
}


def render(run: dict, fmt: str) -> str:
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; use one of {sorted(FORMATS)}")
    return FORMATS[fmt][0](run)


def extension(fmt: str) -> str:
    return FORMATS[fmt][1]


def write(run: dict, fmt: str, directory: str | Path) -> str:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # The format is in the name because condensed and full are both markdown,
    # and one silently overwriting the other is a very quiet way to lose a report.
    path = directory / f"{run['ticker']}-{run['as_of']}-{fmt}.{extension(fmt)}"
    path.write_text(render(run, fmt), encoding="utf-8")
    return str(path)


__all__ = ["FORMATS", "card", "condensed", "extension", "full", "render", "scorecard",
           "write"]
