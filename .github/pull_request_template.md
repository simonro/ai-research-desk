## What this changes

<!-- What and why, in a few sentences. Link the issue if there is one. -->

## How it was tested

<!-- Commands run, and what you checked by hand. -->

- [ ] `python -m pytest tests -q` passes in `desk/` and in `edgedesk/`
- [ ] `python -m pytest -q` passes in `dashboard/` (with `PYTHONPATH=../desk`)
- [ ] `python -m pytest hedge_fund -q` passes in `engines/ai-hedge-fund/` (if touched)
- [ ] Rating, debate or valuation logic changed? Tests with hand-worked expected values are included

## Worth knowing for review

- [ ] New dependency (which, and why)
- [ ] New network call, open port, or background job
- [ ] Change to how model calls are isolated (tools, settings, logins)
- [ ] File access outside the repo and `~/.hedge-desk`
- [ ] None of the above

No API keys, login files, `.env` files or personal positions are included.
