"""Free data stack: Alpaca (prices, news) + SEC EDGAR (fundamentals) + Yahoo
(earnings surprises, consensus, profile). See client.py."""

from hedge_fund.data.free._common import FreeDataError
from hedge_fund.data.free.client import FreeDataClient

__all__ = ["FreeDataClient", "FreeDataError"]
