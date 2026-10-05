"""Memory between runs: what changed this week, and is the thesis still true.

Neither upstream project had this. Each run was a fresh opinion, so a rating
that quietly drifted from Buy to Hold over six weeks looked exactly like a
rating that had always been Hold, and a thesis that had already broken went on
being restated because nobody had written down what it depended on.

Both answers come from run files that already exist, so nothing here needs a
database and nothing needs backfilling. On the first run there is no comparison
to make, and saying so plainly is the correct output, not a gap to apologise for.
"""

from __future__ import annotations

import re

from edgedesk import run as run_mod
from edgedesk.verdict.published import published

# A factor score moving less than this is noise, not news.
_FACTOR_MOVE = 5.0
# A price move worth mentioning in a weekly summary.
_PRICE_MOVE = 0.05


def _pct_change(now, before):
    if now is None or before is None or not before:
        return None
    return round(now / before - 1, 6)


def what_changed(run: dict, previous: dict | None = None) -> dict:
    """This run against the last one for the same ticker."""
    previous = previous or run_mod.previous(run["ticker"], run["as_of"])
    if previous is None:
        return {"first_run": True,
                "summary": "First run for this ticker. The comparison starts next week."}

    out: dict = {"first_run": False, "since": previous["as_of"], "ratings": [],
                 "factors": [], "notes": []}

    for horizon in ("swing", "long_term"):
        now = published(run, horizon)
        was = published(previous, horizon)
        if now.get("rating") != was.get("rating"):
            out["ratings"].append({
                "horizon": horizon,
                "from": was.get("rating") or "withheld",
                "to": now.get("rating") or "withheld",
                "score_from": was.get("score"),
                "score_to": now.get("score"),
            })

    now_factors = run.get("factors") or {}
    was_factors = previous.get("factors") or {}
    for key, fam in now_factors.items():
        before = (was_factors.get(key) or {}).get("score")
        after = fam.get("score")
        if before is None or after is None:
            continue
        move = round(after - before, 2)
        if abs(move) >= _FACTOR_MOVE:
            out["factors"].append({"family": key, "label": fam.get("label", key),
                                   "from": before, "to": after, "move": move})
    out["factors"].sort(key=lambda f: -abs(f["move"]))

    price_now = ((run.get("evidence") or {}).get("anchors") or {}).get("last_close")
    price_was = ((previous.get("evidence") or {}).get("anchors") or {}).get("last_close")
    out["price"] = {"from": price_was, "to": price_now,
                    "change": _pct_change(price_now, price_was)}

    now_state = (run.get("evidence") or {}).get("quality_score")
    was_state = (previous.get("evidence") or {}).get("quality_score")
    if now_state is not None and was_state is not None and now_state != was_state:
        out["notes"].append(f"Data quality moved from {was_state} to {now_state}.")

    new_caveats = {c["code"] for c in (run.get("evidence") or {}).get("caveats") or []}
    old_caveats = {c["code"] for c in (previous.get("evidence") or {}).get("caveats") or []}
    for code in sorted(new_caveats - old_caveats):
        out["notes"].append(f"New data caveat: {code}.")
    for code in sorted(old_caveats - new_caveats):
        out["notes"].append(f"Resolved data caveat: {code}.")

    out["summary"] = _summarize(out)
    return out


def _summarize(changed: dict) -> str:
    parts = []
    for r in changed["ratings"]:
        parts.append(f"{r['horizon'].replace('_', ' ')} moved {r['from']} to {r['to']}")
    price = (changed.get("price") or {}).get("change")
    if price is not None and abs(price) >= _PRICE_MOVE:
        parts.append(f"price {price:+.1%}")
    if changed["factors"]:
        top = changed["factors"][0]
        parts.append(f"{top['label'].lower()} {top['move']:+.0f}")
    if not parts:
        return f"No material change since {changed['since']}."
    return "Since " + changed["since"] + ": " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Thesis health
# ---------------------------------------------------------------------------

_PERSISTENCE = re.compile(r"\b(two|three)\s+consecutive\b", re.I)
_QUARTERS = {"two": 2, "three": 3}


def _quarters_required(condition: dict) -> int:
    """How many consecutive filings must breach before a condition is broken.

    Read from the structured field when the entry recorded one, and from the
    sentence for entries written before it existed, so an old snapshot is held
    to the persistence its own text promised.
    """
    if condition.get("quarters"):
        return int(condition["quarters"])
    match = _PERSISTENCE.search(condition.get("condition") or "")
    return _QUARTERS[match.group(1).lower()] if match else 1


def _breached(value: float, condition: dict) -> bool | None:
    floor, ceiling = condition.get("breaks_below"), condition.get("breaks_above")
    if floor is not None:
        return value < floor
    if ceiling is not None:
        return value > ceiling
    return None


def thesis_health(run: dict, entry: dict | None = None,
                  since: str | None = None) -> dict:
    """The long-term thesis conditions, checked against today's numbers.

    The conditions were written at entry from that day's fundamentals, and they
    are checked against today's. That is the point: a thesis is a claim about
    what must stay true, and the only way to know it broke is to have written it
    down before it did.

    Four states, and the difference between them is the whole design:

    * holding: every reading needed is present and none breaches
    * warning: the latest reading breaches but persistence is not yet met, or the
      condition carries an exception the engine cannot evaluate (an acquisition)
    * broken: the breach has held for as many consecutive filings as the condition
      states
    * unknown: a reading the condition needs is missing. Never folded into holding.

    The readings are trailing-twelve-month figures at successive filings, so "two
    consecutive quarters" means two consecutive TTM readings, not two standalone
    quarters.
    """
    entry = entry or _entry_run(run["ticker"], run["as_of"], since)
    if entry is None:
        return {"first_run": True,
                "summary": ("First run, so the entry snapshot is being saved now. "
                            "Thesis health starts from the next run.")}

    conditions = (((entry.get("levels") or {}).get("long_term") or {})
                  .get("thesis_invalidation") or {}).get("conditions") or []
    rows = (run.get("evidence") or {}).get("metrics") or []
    metric_keys = {
        "Operating margin": "operating_margin",
        "Revenue growth": "revenue_growth",
        "Debt to equity": "debt_to_equity",
        "Return on equity": "return_on_equity",
    }

    checked = []
    for condition in conditions:
        key = metric_keys.get(condition.get("metric"))
        need = _quarters_required(condition)
        readings = [r.get(key) for r in rows[:need]] if key else []
        current = readings[0] if readings else None
        exception = ("without an acquisition" in (condition.get("condition") or "")
                     or condition.get("exception"))
        status, detail = "unknown", "The metric is not available in this run."
        if key and len(readings) == need and all(v is not None for v in readings):
            hits = [_breached(v, condition) for v in readings]
            floor = condition.get("breaks_below")
            limit = floor if floor is not None else condition.get("breaks_above")
            side = "floor" if floor is not None else "ceiling"
            detail = f"{current:.4g} against a {side} of {limit:.4g}"
            if hits[0] is None:
                status = "unknown"
            elif all(hits):
                if exception:
                    status = "warning"
                    detail += ("; the stated exception (an acquisition) is not evaluated "
                               "here, so check it by hand")
                else:
                    status = "broken"
                    if need > 1:
                        detail += f", breached at {need} consecutive filings"
            elif hits[0]:
                status = "warning"
                detail += f", breached at the latest filing only (needs {need} in a row)"
            else:
                status = "holding"
        elif key and current is not None:
            detail = (f"Latest reading is {current:.4g}, but {need} consecutive filings are "
                      "needed and fewer are available.")
        checked.append({"metric": condition.get("metric"), "status": status,
                        "detail": detail, "at_entry": condition.get("now"),
                        "now": current, "condition": condition.get("condition"),
                        "quarters_required": need})

    broken = sum(c["status"] == "broken" for c in checked)
    price_now = ((run.get("evidence") or {}).get("anchors") or {}).get("last_close")
    marker = (((entry.get("levels") or {}).get("long_term") or {})
              .get("thesis_invalidation") or {}).get("price_marker")
    price_broken = bool(price_now and marker and price_now < marker)
    entry_price = ((entry.get("evidence") or {}).get("anchors") or {}).get("last_close")

    return {
        "first_run": False,
        "entry_date": entry["as_of"],
        "entry_basis": (f"owned since {since}" if since
                        else "first run on file; not a recorded purchase date"),
        "entry_price": entry_price,
        "price_now": price_now,
        "since_entry": _pct_change(price_now, entry_price),
        "conditions": checked,
        "broken": broken,
        "warnings": sum(c["status"] == "warning" for c in checked),
        "unknown": sum(c["status"] == "unknown" for c in checked),
        "price_marker": marker,
        "price_marker_breached": price_broken,
        "summary": _health_summary(checked, broken, price_broken),
    }


def _health_summary(checked: list[dict], broken: int, price_broken: bool) -> str:
    if not checked:
        return "No thesis conditions were recorded at entry."

    def names(state: str) -> str:
        return ", ".join(c["metric"].lower() for c in checked if c["status"] == state)

    count = {s: sum(c["status"] == s for c in checked)
             for s in ("warning", "unknown", "holding")}
    parts = []
    if broken:
        parts.append(f"{broken} of {len(checked)} conditions broken ({names('broken')})")
    if count["warning"]:
        parts.append(f"{count['warning']} on warning ({names('warning')})")
    if count["unknown"]:
        parts.append(f"{count['unknown']} could not be checked ({names('unknown')})")
    if price_broken:
        parts.append("the price marker has been breached, which is a prompt to check the "
                     "business, not a signal to sell")
    if not parts:
        return f"All {len(checked)} thesis conditions still hold."
    if count["holding"]:
        parts.append(f"{count['holding']} holding")
    return "Thesis health: " + "; ".join(parts) + "."


def _entry_run(ticker: str, before: str, since: str | None = None) -> dict | None:
    """The earliest run on file for this ticker, which is the entry snapshot.

    Deliberately the earliest rather than a separately frozen file: a snapshot
    that lives in the same place as every other run cannot drift out of sync
    with them, and cannot be lost separately.
    """
    from edgedesk.paths import RUNS_DIR

    if not RUNS_DIR.exists():
        return None
    for day in sorted(d.name for d in RUNS_DIR.iterdir() if d.is_dir()):
        if day >= before:
            break
        if since and day < since:
            continue
        found = run_mod.read(ticker, day)
        if found:
            return found
    return None
