"""Reading the sample: do higher scores actually come before better outcomes?

The question is monotonicity and separation, not the prettiest equity curve. A
band structure is useful if the band above reliably did better than the band
below, by enough to matter, on data the bands were not chosen from. Anything
else is decoration, and an optimizer that searched for the best-looking weights
would produce a great deal of decoration.

So there is no optimizer here. The weights are the ones a human wrote down, the
bands are the ones a human wrote down, and this module's only job is to say
whether they hold up, and to name the bands that do not separate and should be
merged.
"""

from __future__ import annotations

from statistics import mean, median

from edgedesk.verdict.rating import BANDS

# The score buckets the review asked for, coarse on purpose: a sample this size
# cannot support twenty of them, and merging is the recommendation a thin bucket
# should earn.
BUCKETS = ((90, 101, "90-100"), (80, 90, "80-89"), (70, 80, "70-79"),
           (60, 70, "60-69"), (50, 60, "50-59"), (0, 50, "under 50"))
# A band with fewer than this many observations is reported but never used to
# justify a change: the number would be noise wearing a decimal point.
MIN_BAND_N = 20
# Separation worth acting on, in excess return. Below this, two bands are one
# band that happens to have two names.
MIN_SEPARATION = 0.01


def bucket_of(score: float) -> str:
    for low, high, label in BUCKETS:
        if low <= score < high:
            return label
    return BUCKETS[-1][2]


def band_of(score: float) -> str:
    for floor, label in BANDS:
        if score >= floor:
            return label
    return BANDS[-1][1]


def _stats(values: list[float]) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0}
    clean.sort()
    return {
        "n": len(clean),
        "mean": round(mean(clean), 6),
        "median": round(median(clean), 6),
        "p25": round(clean[len(clean) // 4], 6),
        "p75": round(clean[(3 * len(clean)) // 4], 6),
        "win_rate": round(sum(1 for v in clean if v > 0) / len(clean), 4),
    }


def group(observations: list[dict], horizon: str, window: str,
          key=bucket_of) -> dict[str, dict]:
    """Forward-outcome statistics per score bucket, for one horizon and window."""
    rows: dict[str, dict] = {}
    for obs in observations:
        if obs["horizon"] != horizon:
            continue
        outcome = next((o for o in obs["outcomes"] if o["window"] == window), None)
        if outcome is None:
            continue
        label = key(obs["score"])
        row = rows.setdefault(label, {"excess": [], "ret": [], "sector_excess": [],
                                      "mae": [], "mfe": [], "drawdown": []})
        for field in row:
            row[field].append(outcome.get(field))
    return {label: {field: _stats(values) for field, values in row.items()}
            for label, row in rows.items()}


def ordered(labels) -> list[str]:
    """Buckets highest first, so a table reads the way the claim does."""
    order = [label for _, _, label in BUCKETS]
    return [label for label in order if label in labels]


def monotonicity(grouped: dict[str, dict], field: str = "excess") -> dict:
    """Does the median outcome fall as the score bucket falls?

    Reported as the share of adjacent pairs that are in the right order, plus
    the pairs that are not, because which pair broke is more useful than a
    single score.
    """
    labels = [l for l in ordered(grouped) if grouped[l][field]["n"] >= MIN_BAND_N]
    if len(labels) < 2:
        return {"pairs": 0, "in_order": 0, "rate": None, "breaks": [],
                "note": f"fewer than two buckets reached {MIN_BAND_N} observations"}
    breaks, in_order = [], 0
    for upper, lower in zip(labels, labels[1:]):
        hi = grouped[upper][field]["median"]
        lo = grouped[lower][field]["median"]
        if hi >= lo:
            in_order += 1
        else:
            breaks.append({"upper": upper, "lower": lower, "upper_median": hi,
                           "lower_median": lo})
    pairs = len(labels) - 1
    return {"pairs": pairs, "in_order": in_order, "rate": round(in_order / pairs, 4),
            "breaks": breaks}


def merge_candidates(grouped: dict[str, dict], field: str = "excess") -> list[dict]:
    """Adjacent buckets that did not separate, and so should become one.

    Two kinds qualify: a bucket too thin to mean anything, and a pair whose
    medians differ by less than the threshold. Both are recommendations, never
    automatic: changing a band changes every past rating's meaning, and that is
    a decision a person makes.
    """
    labels = ordered(grouped)
    out = []
    for upper, lower in zip(labels, labels[1:]):
        hi_stats, lo_stats = grouped[upper][field], grouped[lower][field]
        if not hi_stats.get("n") or not lo_stats.get("n"):
            continue
        if hi_stats["n"] < MIN_BAND_N or lo_stats["n"] < MIN_BAND_N:
            out.append({"upper": upper, "lower": lower, "reason": "too few observations",
                        "n_upper": hi_stats["n"], "n_lower": lo_stats["n"]})
            continue
        gap = hi_stats["median"] - lo_stats["median"]
        if abs(gap) < MIN_SEPARATION:
            out.append({"upper": upper, "lower": lower, "reason": "no separation",
                        "gap": round(gap, 6), "n_upper": hi_stats["n"],
                        "n_lower": lo_stats["n"]})
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:+.2f}%"


def _pct0(v) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def render(dev: list[dict], holdout: list[dict], meta: dict) -> str:
    out = [
        "# Calibration report",
        "",
        f"Deterministic engine only, no language model anywhere in this path. "
        f"Factors v{meta.get('factors_version')}, rating v{meta.get('rating_version')}.",
        "",
        f"- **Universe:** {meta.get('ticker_count')} tickers",
        f"- **Development period:** {meta.get('dev_start')} to {meta.get('dev_end')}, "
        f"{len(dev)} observations",
        f"- **Held-out period:** {meta.get('holdout_start')} to {meta.get('holdout_end')}, "
        f"{len(holdout)} observations",
        f"- **Sampling:** every {meta.get('step')} trading days",
        *([f"- **Purge (R2-18):** development keeps only outcome windows that closed before the "
           f"held-out period; {meta['purge']['dropped_windows']} windows removed, "
           f"{meta['purge']['dropped_rows']} observations left with none. Effective sample: "
           f"development {meta['purge']['dev_tickers']} tickers on {meta['purge']['dev_dates']} dates, "
           f"held-out {meta['purge']['holdout_tickers']} tickers on {meta['purge']['holdout_dates']} dates. "
           "Weekly observations overlap, so independent results are far fewer than rows."]
          if meta.get("purge") else []),
        "",
        "The held-out period was never looked at while anything was chosen. No weights "
        "were searched or fitted: the question is only whether the weights and bands "
        "already written down separate outcomes.",
        "",
    ]
    for horizon, windows in (("swing", ("10d", "20d", "30d")),
                             ("long_term", ("3m", "6m", "12m"))):
        out += _horizon_section(horizon, windows, dev, holdout)
    out += _verdict(dev, holdout)
    return "\n".join(out)


def _horizon_section(horizon: str, windows, dev, holdout) -> list[str]:
    title = "Swing" if horizon == "swing" else "Long term"
    out = [f"## {title}", ""]
    for window in windows:
        d = group(dev, horizon, window)
        h = group(holdout, horizon, window)
        if not d:
            out += [f"### {window}", "", "No observations.", ""]
            continue
        out += [f"### {window}", "",
                "Excess return against SPY, and against the stock's own sector ETF. The "
                "sector column is the control: on a tech-heavy sample in a tech-led "
                "market, beating SPY can just mean being in technology.", "",
                "| Bucket | n | Median vs SPY | Mean vs SPY | Beat SPY | "
                "Median vs sector | Beat sector | Held-out n | Held-out median |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for label in ordered(d):
            st = d[label]["excess"]
            sec = d[label]["sector_excess"]
            hs = h.get(label, {}).get("excess", {})
            out.append(
                f"| {label} | {st['n']} | {_pct(st.get('median'))} | {_pct(st.get('mean'))} | "
                f"{_pct0(st.get('win_rate'))} | {_pct(sec.get('median'))} | "
                f"{_pct0(sec.get('win_rate'))} | "
                f"{hs.get('n', 0)} | {_pct(hs.get('median'))} |")
        out.append("")
        sector_mono = monotonicity(d, "sector_excess")
        if sector_mono.get("rate") is not None:
            out.append(f"Against the sector: {sector_mono['in_order']} of "
                       f"{sector_mono['pairs']} buckets ordered "
                       f"({_pct0(sector_mono['rate'])}).")
            out.append("")
        mono = monotonicity(d)
        if mono.get("rate") is None:
            out.append(f"Monotonicity: {mono['note']}.")
        else:
            out.append(f"Monotonicity: {mono['in_order']} of {mono['pairs']} adjacent "
                       f"buckets in the right order ({_pct0(mono['rate'])}).")
            for b in mono["breaks"]:
                out.append(f"- **{b['upper']}** ({_pct(b['upper_median'])}) did worse than "
                           f"**{b['lower']}** ({_pct(b['lower_median'])}).")
        merges = merge_candidates(d)
        if merges:
            out.append("")
            out.append("Buckets that did not earn their separation:")
            for m in merges:
                detail = (f"gap {_pct(m['gap'])}" if "gap" in m
                          else f"n={m['n_upper']} and {m['n_lower']}")
                out.append(f"- {m['upper']} and {m['lower']}: {m['reason']} ({detail}).")
        out.append("")
    return out


def _verdict(dev, holdout) -> list[str]:
    """The honest summary, including the case where the answer is "not yet"."""
    out = ["## What this says", ""]
    if len(dev) < MIN_BAND_N * 3:
        out += [
            f"The development sample holds {len(dev)} observations, which is too few to "
            "move any band. Treat this run as a check that the harness works and that "
            "nothing leaked, not as evidence about the scores.",
            "",
        ]
    lines = []
    for horizon, window in (("swing", "20d"), ("long_term", "6m")):
        d = group(dev, horizon, window)
        h = group(holdout, horizon, window)
        if not d:
            continue
        mono_dev = monotonicity(d)
        mono_hold = monotonicity(h)
        label = "Swing at 20 days" if horizon == "swing" else "Long term at 6 months"
        if mono_dev.get("rate") is None:
            lines.append(f"- **{label}:** {mono_dev['note']}.")
            continue
        mono_sector = monotonicity(d, "sector_excess")
        lines.append(
            f"- **{label}:** {_pct0(mono_dev['rate'])} of adjacent buckets ordered against "
            "SPY on the development period"
            + (f", {_pct0(mono_sector['rate'])} against the sector"
               if mono_sector.get("rate") is not None else "")
            + (f", {_pct0(mono_hold['rate'])} on the held-out period."
               if mono_hold.get("rate") is not None
               else ", and the held-out period had too few observations to check."))
    out += lines or ["- No horizon had enough complete windows to assess."]
    out += [
        "",
        "A band structure earns trust when the ordering holds on the held-out period as "
        "well as the development one. Where it does not, the honest reading is that the "
        "score does not yet separate outcomes at that horizon, and the rating should be "
        "described as a consistent opinion rather than a validated one.",
        "",
        "Nothing here changes a band automatically. Merging or moving one changes what "
        "every past rating meant, so it is a decision a person makes, on evidence, and "
        "records with a new rating version.",
    ]
    return out
