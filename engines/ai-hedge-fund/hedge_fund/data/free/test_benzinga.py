"""Benzinga earnings-headline parsing tests."""

from datetime import datetime, timezone

from hedge_fund.data.free.benzinga import first_release, parse_headline

T = datetime(2026, 7, 29, 20, 6, tzinfo=timezone.utc)


def test_current_template_with_sales():
    r = parse_headline("Microsoft Q4 Adj. EPS $4.74 Beats $4.24 Estimate, "
                       "Sales $90.007B Beat $87.621B Estimate", T)
    assert (r.eps, r.eps_estimate) == (4.74, 4.24)
    assert r.revenue == 90.007e9 and r.revenue_estimate == 87.621e9
    assert r.kind_rank == 0


def test_older_template_and_negative_numbers():
    r = parse_headline("Microsoft Reports Q2 Adj. EPS $0.96 vs $0.86 Est.", T)
    assert (r.eps, r.eps_estimate) == (0.96, 0.86)
    r = parse_headline("Rivian Q1 EPS $(1.25) Misses $(1.10) Estimate", T)
    assert (r.eps, r.eps_estimate) == (-1.25, -1.10)


def test_non_comparable_and_non_template_headlines_are_ignored():
    assert parse_headline("Microsoft Corp Reports Q2 GAAP EPS $(0.82) May Not Compare "
                          "To $0.86 Est., Sales $28.92B vs $28.39B Est.", T) is None
    assert parse_headline("JPMorgan Chase Q4 Avg. Loans Up 9% YoY, AUM Up 18%", T) is None
    assert parse_headline("Microsoft shares rise after EPS beat", T) is None


def _item(ts, headline, symbols=("MSFT",)):
    return {"created_at": ts, "headline": headline, "symbols": list(symbols)}


def test_first_release_prefers_adjusted_and_skips_roundups():
    items = [
        _item("2026-07-30T13:00:00Z", "Top 5 Q4 EPS $1.00 Beats $0.90 Estimate",
              symbols=("MSFT", "AAPL", "NVDA", "AMZN")),
        _item("2026-07-29T20:07:00Z", "Microsoft Q4 GAAP EPS $4.50 Beats $4.24 Estimate"),
        _item("2026-07-29T20:06:00Z", "Microsoft Q4 Adj. EPS $4.74 Beats $4.24 Estimate"),
    ]
    r = first_release(iter(items), "MSFT")
    assert r.eps == 4.74


def test_first_release_stops_reading_a_day_past_the_match():
    consumed = []

    def stream():
        for item in [
            _item("2026-07-29T20:06:00Z", "Microsoft Q4 Adj. EPS $4.74 Beats $4.24 Estimate"),
            _item("2026-07-27T10:00:00Z", "Microsoft previews its quarter"),
            _item("2026-04-29T20:06:00Z", "Microsoft Q3 Adj. EPS $4.27 Beats $4.06 Estimate"),
        ]:
            consumed.append(item)
            yield item

    assert first_release(stream(), "MSFT").eps == 4.74
    assert len(consumed) == 2  # never paged back to the prior quarter
