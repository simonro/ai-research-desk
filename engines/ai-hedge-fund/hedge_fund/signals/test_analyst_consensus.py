"""AnalystConsensusModel tests: scoring, and abstaining wherever data is absent or not point-in-time."""

from datetime import date, timedelta

from hedge_fund.data.models import AnalystConsensus
from hedge_fund.signals import ALPHA_MODEL_REGISTRY
from hedge_fund.signals.analyst_consensus import AnalystConsensusModel

TODAY = date.today().isoformat()


class ConsensusClient:
    def __init__(self, consensus=None, error=None):
        self._consensus, self._error = consensus, error

    def get_analyst_consensus(self, ticker):
        if self._error:
            raise self._error
        return self._consensus


def _consensus(mean=1.5, count=40, target=120.0, price=100.0):
    return AnalystConsensus(ticker="MSFT", fetched_on=TODAY, recommendation_mean=mean,
                            recommendation_key="buy", analyst_count=count,
                            target_mean_price=target, current_price=price)


def test_scores_rating_and_upside():
    signal = AnalystConsensusModel().predict("MSFT", TODAY, ConsensusClient(_consensus()))
    # rating (2.5 - 1.5) / 1.5 = 0.6667; upside 20% / 30% = 0.6667
    assert signal.value == 0.6667
    assert "40 analysts" in signal.reasoning
    assert not signal.metadata.get("abstained")


def test_bearish_street_goes_negative():
    signal = AnalystConsensusModel().predict(
        "X", TODAY, ConsensusClient(_consensus(mean=4.0, target=80.0, price=100.0)))
    assert signal.value < 0


def test_abstains_on_backtest_dates():
    old = (date.today() - timedelta(days=30)).isoformat()
    signal = AnalystConsensusModel().predict("MSFT", old, ConsensusClient(_consensus()))
    assert signal.value == 0.0 and signal.metadata["abstained"]


def test_abstains_without_provider_support_thin_coverage_or_fetch_errors():
    model = AnalystConsensusModel()
    assert model.predict("MSFT", TODAY, object()).metadata["abstained"]
    assert model.predict("MSFT", TODAY, ConsensusClient(_consensus(count=2))).metadata["abstained"]
    assert model.predict("MSFT", TODAY, ConsensusClient(error=RuntimeError("429"))).metadata["abstained"]


def test_registered():
    assert ALPHA_MODEL_REGISTRY["analyst_consensus"] is AnalystConsensusModel
