# Contributing to ai-research-desk

Thanks for helping. Bug reports, fixes and features are all welcome. This page covers how to set
up, what to test, and what a pull request needs before it can be merged.

**Security problems:** please do not open a public issue. Follow [SECURITY.md](SECURITY.md) and use
GitHub's private vulnerability reporting.

## What the project cares about

These decide most review questions, so they are worth knowing before you start:

1. **Research only.** The desk never places orders, connects to a brokerage account, or opens a
   market-data websocket. A change that moves it toward trading will not be merged.
2. **Local first.** Everything runs on the user's machine; the dashboard listens on `localhost`
   only. No accounts, no telemetry. A change that sends data somewhere new needs an issue first.
3. **Numbers come from code, not from a model.** Prices, valuation, levels and the stale-filing
   rule are computed. Models write arguments and memos, and every structured answer is checked
   against its schema in code. Figures a model quotes are checked against the evidence.
4. **It refuses rather than guesses.** A missing figure is unknown, not zero. Stale data withholds
   a rating. A failed step is shown as failed. A spent plan or an expired login stops the run and
   saves nothing.
5. **Model calls stay isolated.** Every call, on either plan, runs with no tools, no web search, no
   user settings and no file access. Do not loosen this.
6. **Small and focused.** One pull request, one change.

## Setting up

Requirements: Python 3.12, git, and the CLI for your plan (Claude Code's `claude`, or OpenAI's
`codex`).

On Windows, run `setup.bat --dev` once. Anywhere:

```bash
git clone --recurse-submodules https://github.com/simonro/ai-research-desk
cd ai-research-desk
python3.12 install.py --dev
```

A new install is always empty: no runs, no demo data. **Never use or commit real data**: no API
keys, login files or personal positions in tests, fixtures, screenshots or issues.

## Tests

CI runs these on every pull request, and they must pass. Each part uses its own environment:

```bash
cd desk && .venv/bin/python -m pytest tests -q
cd dashboard && PYTHONPATH=../desk ../desk/.venv/bin/python -m pytest -q
cd edgedesk && .venv/bin/python -m pytest tests -q
cd engines/ai-hedge-fund && .venv/bin/python -m pytest hedge_fund -q
```

(On Windows use `.venv\Scripts\python.exe`.) No test calls a model or a market-data service:
plans and data sources are faked.

What to add:

- **A fix:** a test that fails without it.
- **Rating, debate, valuation or data logic:** fixtures with hand-worked expected values.
- **A change to model-call isolation:** a test that the new path is still locked down.
- **A UI change:** a screenshot in the pull request.

## Adding a plan (model provider)

The desk reaches models only through subscription CLIs. To add one:

1. Write a backend like `desk/desk/chatgpt.py`: build an isolated command (no tools, no user
   settings, read-only, no session saved), run it with the prompt on stdin, and return the text,
   the parsed JSON and the token usage.
2. Route to it from `plan()` and `ask()` in `desk/desk/maxplan.py`, and add its usage-limit and
   login wording to the shared stop pattern.
3. Mirror it in `edgedesk/edgedesk/llm/headless.py` (Edge Desk is installed separately).
4. Add tests in `desk/tests/test_chatgpt.py` style with the CLI faked.

## Pull requests

- Branch from `main` and keep the change focused.
- Describe what changed and why, and how you tested it.
- Say so if the change adds a dependency, a network call, or file access outside the repo and
  `~/.hedge-desk`. New dependencies need a reason.
- Saved runs from older versions must keep opening in the dashboard.
- Never include `.env` files, API keys, login files or run memos.

## Code style

Match the code around you. Comments explain why something is done, not what the next line does.
Keep user-facing text plain and specific, with no em or en dashes.

## Questions

Ask in [Discussions](https://github.com/simonro/ai-research-desk/discussions). Never paste keys or logins.
