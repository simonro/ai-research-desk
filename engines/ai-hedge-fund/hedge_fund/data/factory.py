"""Pick the data provider: Financial Datasets (paid) or the free stack.

    HEDGE_FUND_DATA_PROVIDER=free                 Alpaca + SEC EDGAR + Benzinga/Yahoo
    HEDGE_FUND_DATA_PROVIDER=financialdatasets    the original FDClient

Unset, the choice follows the keys you have: a Financial Datasets key means
FD, otherwise free. Entry points call make_data_client() instead of naming a
client, so switching providers is an env var, not a code change.
"""

from __future__ import annotations

import os

PROVIDER_ENV = "HEDGE_FUND_DATA_PROVIDER"
FD_KEY_ENV = "FINANCIAL_DATASETS_API_KEY"

_ALPACA_CREDENTIALS = (
    ("Alpaca (key id)", "ALPACA_API_KEY"),
    ("Alpaca (secret)", "ALPACA_SECRET_KEY"),
)
_SEC_CREDENTIAL = ("SEC EDGAR User-Agent (your name + contact)", "SEC_USER_AGENT")


def data_provider() -> str:
    explicit = os.environ.get(PROVIDER_ENV, "").strip().lower()
    if explicit in ("fd", "financialdatasets", "financial_datasets"):
        return "financialdatasets"
    if explicit == "free":
        return "free"
    if explicit:
        raise ValueError(f"{PROVIDER_ENV}={explicit!r}: use 'free' or 'financialdatasets'")
    return "financialdatasets" if os.environ.get(FD_KEY_ENV) else "free"


def make_data_client():
    """A fresh raw client for the selected provider (a context manager)."""
    if data_provider() == "free":
        from hedge_fund.data.free import FreeDataClient

        return FreeDataClient()
    from hedge_fund.data.client import FDClient

    return FDClient()


def missing_data_credentials() -> list[tuple[str, str]]:
    """(label, env var) for every credential the selected provider still needs."""
    if data_provider() != "free":
        needed = [("Financial Datasets", FD_KEY_ENV)]
    else:
        from hedge_fund.data.free.alpaca import resolve_credentials

        # Any accepted spelling, or a shared .env via ALPACA_ENV_FILE, counts.
        needed = ([] if all(resolve_credentials()) else list(_ALPACA_CREDENTIALS)) + [_SEC_CREDENTIAL]
    return [(label, env) for label, env in needed if not os.environ.get(env)]
