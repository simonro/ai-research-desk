# The Desk dashboard

A local page for the desk (TradingAgents + ai-hedge-fund + the Super Manager). It replaces Desk
Theater. Start a symbol, watch both teams work, reopen any past run.

## Run it

Double-click `Run Dashboard.bat` in the Hedge Fund folder, or:

```
desk\.venv\Scripts\python.exe dashboard\server.py
```

Then open http://localhost:8790. Closing the window stops the page; a run already started keeps
going to the end and shows up under Runs when you reopen the page.

## What it does

- **Run a symbol**: ticker, New position / Owned, which teams (Quant desk, Veterans, Edge Desk),
  and whether they debate (needs two or more). This starts `python -m desk TICKER --engines ...`
  (plus `--no-debate` for reports only), exactly as from the terminal, all on the Max plan.
  With a debate you get the horizons, the debate and a memo; an unresolved 2 vs 1 is shown as
  Gridlock with no rating. Without one you get each team's rating side by side and its report.
- **Live**: each team's agents as they finish, a feed of findings, then the horizon comparison
  and the debate turn by turn. When the memo is written the page becomes the snapshot.
  If a run stops, Run again reuses what finished earlier that day on the same plan, settings and
  price session. Every run keeps its own folder, so a rerun never replaces an earlier one; the
  day's latest opens by default and the others are listed under "Other runs".
- **Snapshot**: symbol card, the call per horizon (rating, action, levels, chart, valuation),
  where the teams disagreed, how each team got there, and the full reports.
- **Reader**: the combined memo, and each team's report laid out as its own tool writes it.
  Summaries only folds the Quant desk's report to one line per agent.
- **Symbol card**: Yahoo profile (description, sector and industry, market cap, P/E, next
  earnings, dividend, beta, 52-week range), cached a day in `dashboard/cache/`. Logo from
  Financial Modeling Prep's public image, then the company site's icon, then a letter tile.

## Files

| File | Job |
|---|---|
| `server.py` | local server (127.0.0.1:8790), starts desk runs, streams their events |
| `runview.py` | shapes a finished run (memo JSON, engine reports, events, bars) for the page |
| `symbol_card.py` | Yahoo profile and logo, cached |
| `web/` | the page: `index.html`, `app.css`, `app.js` (no build step) |
| `replay_run.py` | test tool: replays a recorded run's events, no model or broker calls |

## Testing without paying for a run

```
set DESK_REPLAY=MSFT-2026-09-15
desk\.venv\Scripts\python.exe dashboard\server.py --port 8791
```

Any symbol you start then replays that recorded run at 12x. `DESK_REPLAY=MSFT-2026-09-15:14`
stops after 14 events with an error, to see the failure banner. `DESK_REPLAY_SPEED` changes the
speed. Tests: `desk\.venv\Scripts\python.exe -m pytest dashboard`.

## Rules it keeps

Alpaca is REST only (chart bars, cached per run); it never opens a websocket. Company data is
current, not point-in-time, and the card says so; the call's price and 52-week range come from
the run's settled close. Research only: nothing here places orders.
