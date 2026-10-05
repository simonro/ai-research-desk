# Edge Desk: how to use it

Everything below runs from the project directory:

```
ai-research-desk/edgedesk
```

and every command starts with `.venv\Scripts\edgedesk.exe`. If you would rather
type `edgedesk`, activate the venv first with `.venv\Scripts\activate`.

---

## 1. Where your lists live

One file, `~/.edge-desk/universe.yaml`, full path:

```
~/.edge-desk/universe.yaml
```

It has three lists and the difference between them is attention, not quality:

```yaml
owned:            # positions you hold
  - MSFT
  - AVGO

watchlist:        # candidates you are following
  - NVDA
  - MRVL
  - ECG

large_cap:        # a wide list, scored for free, never written up
  - AAPL
  - GOOGL
  - ...
```

- **owned** and **watchlist** get the full treatment: evidence, factors, levels,
  three reports, and the written thesis. They are the only names the LLM layer
  touches.
- **owned** additionally gets **thesis health**: the conditions written down the
  first time it was analyzed are re-checked against each new filing.
- **large_cap** gets the deterministic score only. Free, fast, no model. It
  exists so a name can earn its way onto the watchlist by hand.

Owning a stock changes the *wording of the action* ("trim into strength" rather
than "avoid") and nothing else. It never changes the rating.

Right now `owned` is empty and `watchlist` has the five names already researched.
**Fill in `owned` and thesis health starts working from your next run.**

---

## 2. The four ways to run it

### One name, right now

```bash
.venv\Scripts\edgedesk.exe analyze NVDA
```

Prints the scorecard. Takes about twenty seconds. No model involved, so it costs
nothing and always works.

Useful variations:

```bash
# the written thesis as well (about six minutes, uses your Max subscription)
.venv\Scripts\edgedesk.exe analyze NVDA --llm

# several names at once
.venv\Scripts\edgedesk.exe analyze NVDA AVGO MSFT

# tell it you hold one, which changes the action wording only
.venv\Scripts\edgedesk.exe analyze MSFT --own MSFT

# a different output format
.venv\Scripts\edgedesk.exe analyze NVDA --format condensed
.venv\Scripts\edgedesk.exe analyze NVDA --format card

# save files instead of just printing
.venv\Scripts\edgedesk.exe analyze NVDA --out reports --also card condensed full

# update the Obsidian note for that ticker
.venv\Scripts\edgedesk.exe analyze NVDA --vault

# what did this look like on a past date, using only what was public then
.venv\Scripts\edgedesk.exe analyze NVDA --as-of 2026-06-30
```

### The weekly job

```bash
.venv\Scripts\edgedesk.exe week --out reports --vault
```

Runs every name in `owned` plus `watchlist`, writes all four report formats plus
the Obsidian notes, and prints a summary: what is actionable now, what moved,
whose thesis conditions broke, and what was withheld and why.

The numbers are produced first and fast, then the written analysis is added on
top three names at a time. If the writing fails or you run out of subscription,
you still have every rating, level and report.

```bash
# numbers only, no model, costs nothing
.venv\Scripts\edgedesk.exe week --no-llm
```

**Run it outside 09:30 to 16:00 ET.** It makes a lot of requests on the Alpaca
key your live trading bot shares, and it will refuse to start during market hours
unless you pass `--force`.

### The event check, between weekly runs

```bash
.venv\Scripts\edgedesk.exe events
```

Takes seconds, uses no model, and asks one question per name: has anything
happened since the last run that would change the answer? It flags a new filing,
a new earnings report, a price move beyond what that stock normally does in the
elapsed time, or a headline matching the short list of things that actually move
a thesis (guidance, an acquisition, an investigation, a CEO departure).

It flags names and stops. It never reruns them itself, so it is safe to run as
often as you like.

### The wide scan

```bash
.venv\Scripts\edgedesk.exe scan
```

Scores the whole `large_cap` list deterministically and prints a ranked table.
No model, no cost. Nothing is promoted automatically: move a name into
`watchlist` by hand if it earns the attention.

### Re-reading something already run

```bash
.venv\Scripts\edgedesk.exe report AVGO --format full
```

Re-renders a saved run in a different format **without fetching anything**. A
report is a view of a run, and looking at it differently must never change it.

---

## 3. Reading the output

### The swing block

```
SWING (2 DAYS TO 8 WEEKS)   BUY   score 75/100 (Buy at 60)
  Buy: score 75 of 100 (Buy at 60). Most in favour: setup (long-term uptrend: above a
  rising 200-day; 4.0 ATR under its 20-session high).
  Action: Candidate: the plan below is the whole trade
  Entry         $1,152.93   The next open, near the last close. Do not chase...
  Invalidation  $1,093.98   2 ATR below the close...
  Size          16 shares, about $18,447, risking $1,000 ($58.95 a share)
  Target 1      $1,271.22   2.0R, nearest structure at least 1R away
  Target 2      $1,292.65   2.4R, next structure at least 2R away
  Time limit    40 sessions
  How it was reached:
    setup (weight 30): +10.2 points. ... [tested: held in development and held out]
    earnings (weight 20): +2.8 points. ...
    sentiment (weight 15): +1.1 points. ... [untested: no free history, forward test only]
```

**The call is Buy or Sell, and you decide whether to take it.** Buy needs three
things: a score of 60 or more, at least one live setup, and no blocker. The setups are a
deep dip (down 8% in five sessions), a dip, a dip the buyers took back (a close above
the prior high on a strong candle), a leader under accumulation, a breakout from a
coiled range on volume, and a 3 ATR slide from the high. Each one prints its tested
record beside it; only the deep dip beat buying on a random day, so read that line
before you take a Buy from any of the others. Earnings inside a week is a blocker,
because a report can gap straight through any invalidation. Everything else is Sell,
which for a long-only book means do not buy, and exit or tighten if you hold it. Most
names are a Sell on most days; that is what a selective call looks like. When a strong
name is a Sell only because it has not pulled back, the action line gives the price
that would make it a candidate.

**Forward earnings show up in two places.** In the swing call, the `revisions` part
(weight 10) reads how next year's EPS estimate has moved over 30 and 90 days and how
many analysts raised against how many cut. Rising estimates are one of the
best-documented tailwinds at this horizon, though this engine has no history to test it
on yet, and the line says so. The forward P/E is printed there for context and is not
scored. In the long-term case, the Street's revenue growth for next year, cut by 15%
because analysts run high, is averaged with trailing growth to set where the scenarios
start. The forward P/E is printed beside the multiple of owner earnings, and it is always
the lower of the two: forward EPS is the adjusted figure, which leaves out stock pay.

**Read the plan in the order it was built.** The invalidation comes first, from the
chart: a confirmed swing low, the 100 or 200-day average, or the 20-session low, if one
sits 1.5 to 3 ATR below price, otherwise 2 ATR. Shares are $1,000 divided by the
distance from entry to invalidation. Targets are the nearest real levels overhead that
are at least 1R and 2R away. Change the risk budget in `verdict/swing.py`
(`RISK_DOLLARS`).

**Every component says how much to trust it.** "Tested" means it held in both the
2019 to 2023 and the 2024 to 2026 samples in `docs/SWING-EVIDENCE-2026-09.md`.
"Untested" means there is no free history for it (sentiment, analyst targets), so it
carries a small weight until it earns more by being right going forward. The closing
paragraph states what is known about the call as a whole: a modest, unproven edge, with
40 to 55 percent of Buys expected to win.

**On managing the trade.** Testing found that fixed targets lowered the average result
in every setup, and that one-candle stops get taken out by noise. Holding to the
invalidation or the 40-session limit paid best; taking half at the first target and
moving the stop to entry wins more often and makes less. Both are printed; the choice
is yours.

### The long-term block

```
LONG TERM (1 YEAR OR MORE)
  BUY   score 77.0/100   High conviction
  Action: Start a position in the entry zone
  Tested: exploratory, not independently tested...
  Accumulate at or below $531.85   (clear bargain under $483.37)   Trim $715.00
```

The **Tested** line is the one to read carefully. The long-term score has only been
explored, not independently tested. High scores did beat SPY over twelve months in the
2024 to 2026 sample, but 77 of the 91 high-score observations were NVDA and no held-out
data exists yet, so read the score as a consistent screen rather than a forecast. Between
50 and 79 there is no usable ordering, so treat anything in the middle as undifferentiated
whatever band it lands in.

### The long-term business case

```
LONG-TERM BUSINESS CASE (1 TO 5 YEARS)   BUY   expected +14.6% a year against a 10% hurdle
  Expected return 14.6% a year against a 10% hurdle (bear -2.7%, base 16.5%, bull 28.3%).
  What the price assumes: the price needs only -1% a year against 11% delivered.
  Revenue growth is steady and operating margins are expanding over the last 8 filings.
  Strengths: return on invested capital 63%; the share count is shrinking 4.8% a year.
  Owner earnings $8.3B (35% of revenue; $8.3B with all capital spending charged)...
  Bear  growth 5% fading to 3%, margin 26%, exit 10x ...: $236.10 in 5 years, -2.7% a year
  Base  growth 11% fading to 6%, margin 35%, exit 14x ...: $553.22 in 5 years, +16.5% a year
  Bull  ...
```

This block is the argument; the block above it is the factor screen. They are built
differently and are allowed to disagree. When they do, the disagreement is the thing to
read: a screen Buy with a business-case Sell usually means a wonderful company at a
price that already assumes it.

Four questions, in order. **Owner earnings**: operating cash flow less stock
compensation and the capital spending needed to stand still. Spending above
depreciation is treated as growth and not charged, and the strict figure with all of it
charged is printed beside it. When those two differ a lot (a company in a build-out),
that gap is the question to have a view on. **Trajectory**: eight filings of slope for
growth and margins. **What the price assumes**: a reverse DCF solved for the growth the
price needs, beside what the company has delivered. **Scenario return**: three
five-year paths with every assumption printed, weighted 25/50/25 into an expected
annual return.

Buy needs the 10% hurdle plus 3 points. A Buy is held back to Hold by a bear case worse
than -10% a year, by two or more earnings-quality flags, or by a price that needs more
growth than a slowing company has delivered. Within 3 points of the hurdle is Hold.
Under that, or with no positive owner earnings at all, is Sell.

The assumptions are judgement written down, not a fitted model: a mature company's
growth fades to 6% by year five, while a fast grower keeps 30% of its growth above 6%
(capped at 15%) and is assumed to sell at a higher multiple for it, more so with a return
on capital of 30% or more; an expensive stock is assumed to be sold at a lower multiple than it was bought, a
cheap one closes only a quarter of the gap upward. They are constants at the top of
`evidence/business.py`, each with its reason. Use the expected return to compare
companies on the same rules, not as a forecast.

"Accumulate at or below" is a ceiling, not a band. Cheaper is better. That is the
opposite of the swing entry zone, where below the zone means the setup broke.

### The research layer (only with `--llm`)

Three reads by the model, each with a fence, printed under "Research layer". None of
them changes a number above it.

* **Second look at the swing call.** Take, pass or wait, with up to three concerns the
  numbers cannot see (a story running through the headlines, a crowded trade, a pending
  event). Each concern says what it is based on. It is an opinion beside the call.
* **What the headlines say.** The model labels each headline from the last 14 days:
  about this company or not, positive or negative over weeks, material or noise. The
  tally is arithmetic and is shown beside the keyword count the score uses.
* **What the filings say.** Management's discussion from the newest 10-Q and 10-K and
  the opening of the 10-K risk factors. Every finding carries a quote, and the code checks
  that quote against the filing text: a finding whose quote is not there is deleted, and
  so is one whose numbers are not in its quote. With a verified finding behind it, the
  model may move growth, margin or the exit multiple by one step (15%, 10%, 10%), and
  the result is a "researched case" printed beside the formula's business case.

The SEC archive that serves filing text refuses requests without a contact email. If
the filings section says so, set `SEC_USER_AGENT=Your Name you@example.com` in
`~/.edge-desk/.env`. Everything else works without it. A full run with the research
layer is about 10 model calls per name.

### Withheld

```
  RATING WITHHELD
    - A 10-Q for the period ending 2026-06-30 was filed on 2026-07-29, but
      EDGAR's companyfacts feed still stops at 2026-03-31.
```

Not a view, a data problem. The engine refuses to rate rather than rate on
numbers it knows are superseded. Visa and TSM are both withheld today for
different reasons.

---

## 4. What it costs

| Command | Model calls | Time |
|---|---|---|
| `analyze TICKER` | none | ~20 seconds |
| `analyze TICKER --llm` | 7 | ~6 minutes |
| `week --no-llm` | none | ~2 minutes for 5 names |
| `week` (with writing) | 7 per name | ~15 minutes for 5 names |
| `events` | none | seconds |
| `scan` | none | ~5 minutes for 30 names |

The model calls run on your Max subscription, not the metered API. If you run out
mid-job it stops and says so rather than failing confusingly.

---

## 5. Where things end up

```
~/.edge-desk/runs/2026-09-15/AVGO.json     the run itself, the source of truth
~/.edge-desk/estimates/2026-09-15/AVGO.json  a consensus snapshot, kept for later
~/.edge-desk/universe.yaml                 your three lists
~/.edge-desk/calibration/                  calibration samples and reports
~/.edge-desk/research/                     strategy research: bars, trades, reports, experiment log
reports/AVGO-2026-09-15-card.html          if you passed --out
Obsidian: 03 Investing/Research/Engine/     if you passed --vault
```

The run JSON is the only thing that matters. Every report is rendered from it, so
they cannot disagree, and any of them can be regenerated at any time.

The estimate snapshots are written from day one even though nothing reads them
yet. Analyst consensus has no history anywhere free, so a snapshot not taken
today is gone for good. In a few months they become revision analysis.

---

## 6. A sensible routine

**Sunday evening or Monday before the open:**

```bash
.venv\Scripts\edgedesk.exe week --out reports --vault
```

Read the summary, then the condensed report for anything that moved.

**Midweek, whenever you think of it:**

```bash
.venv\Scripts\edgedesk.exe events
```

If something is flagged, run that one name:

```bash
.venv\Scripts\edgedesk.exe analyze AVGO --llm
```

**Occasionally:**

```bash
.venv\Scripts\edgedesk.exe scan
```

and promote anything interesting into the watchlist by hand.

---

## 7. The standing rules

- **Research only.** It places no orders and gives no advice. Every decision is
  yours.
- **Outside market hours** for `week`, `scan` and `calibrate`. They share an
  Alpaca rate limit with the live trading bot.
- **Never open an Alpaca websocket.** The Scanner owns the account's only one.
  Edge Desk is REST only and always will be.
