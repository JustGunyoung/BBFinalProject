"""Tests for tracker.stats.compute_stats using small hand-made histories."""
from fractions import Fraction

import pytest

from tracker.stats import compute_stats

T0 = 1_764_000_000  # 2025-11-24 16:00 UTC
ADDR = "0x" + "ab" * 20


def make_snap(block, ts=None, buy=(100, 100), sell=(100, 100), price=(1.0, 1.0),
              status="ok", fees=(3000, 10000), price_check="ok"):
    ts = T0 + block * 12 if ts is None else ts
    pools = []
    for i, fee in enumerate(fees):
        pools.append({
            "fee": fee, "pool": ADDR, "sqrt_price_x96": "1", "tick": 0,
            "liquidity": "1", "price_weth_per_link": price[i],
            "price_check": price_check,
            "quote_buy_link_out": None if buy[i] is None else str(buy[i]),
            "quote_sell_weth_out": None if sell[i] is None else str(sell[i]),
        })
    return {"schema_version": 1, "block": block, "timestamp": ts,
            "base_fee_wei": "1", "rpc_status": status, "buy_size_weth": "1",
            "sell_size_link": "1", "pools": pools, "oracle_raw": None}


def test_empty_history():
    s = compute_stats([])
    assert s["window"] is None and s["start_ts"] is None and s["end_ts"] is None
    assert s["snapshots"] == 0
    assert s["drift"] == {"threshold": 0.005, "compared": 0, "rate": None}
    for d in ("buy", "sell"):
        assert s[d]["compared"] == 0
        assert s[d]["median_advantage"] is None and s[d]["max_advantage"] is None
        assert s[d]["wins"] == {"tie": 0}


def test_empty_history_with_explicit_fees():
    s = compute_stats([], fee_tiers=[3000, 10000])
    assert s["buy"]["wins"] == {"3000": 0, "10000": 0, "tie": 0}
    assert s["buy"]["win_rate"] == {"3000": 0.0, "10000": 0.0}


def test_win_counts_rates_median_max():
    hist = [
        make_snap(1, buy=(110, 100)),   # 3000 wins, +10%
        make_snap(2, buy=(130, 100)),   # 3000 wins, +30%
        make_snap(3, buy=(100, 150)),   # 10000 wins, +50%
        make_snap(4, buy=(100, 100)),   # tie
    ]
    b = compute_stats(hist)["buy"]
    assert b["compared"] == 4
    assert b["wins"] == {"3000": 2, "10000": 1, "tie": 1}
    assert b["win_rate"] == {"3000": 0.5, "10000": 0.25}
    # advantages: 0.1, 0.3, 0.5 -> median 0.3
    assert b["median_advantage"] == float(Fraction(3, 10))
    assert b["max_advantage"] == 0.5


def test_median_even_count():
    hist = [make_snap(1, sell=(110, 100)), make_snap(2, sell=(130, 100))]
    s = compute_stats(hist)["sell"]
    assert s["median_advantage"] == pytest.approx(0.2)
    assert s["max_advantage"] == pytest.approx(0.3)


def test_all_ties_have_no_advantage():
    s = compute_stats([make_snap(1), make_snap(2)])["buy"]
    assert s["wins"]["tie"] == 2 and s["compared"] == 2
    assert s["median_advantage"] is None and s["max_advantage"] is None
    assert s["win_rate"] == {"3000": 0.0, "10000": 0.0}


def test_buy_and_sell_independent():
    s = compute_stats([make_snap(1, buy=(2, 1), sell=(1, 2))])
    assert s["buy"]["wins"]["3000"] == 1
    assert s["sell"]["wins"]["10000"] == 1


def test_null_quote_excluded_from_that_direction_only():
    hist = [make_snap(1, buy=(None, 100), sell=(120, 100)),
            make_snap(2, buy=(120, 100), sell=(100, None))]
    s = compute_stats(hist)
    assert s["buy"]["compared"] == 1 and s["buy"]["wins"]["3000"] == 1
    assert s["sell"]["compared"] == 1 and s["sell"]["wins"]["3000"] == 1
    assert s["snapshots"] == 2
    assert s["drift"]["compared"] == 2


def test_null_price_and_failed_check_excluded_from_drift_only():
    hist = [make_snap(1, price=(None, 1.0)),
            make_snap(2, price=(1.0, 1.1), price_check="failed"),
            make_snap(3, price=(1.0, 1.1))]
    s = compute_stats(hist)
    assert s["drift"]["compared"] == 1 and s["drift"]["rate"] == 1.0
    assert s["buy"]["compared"] == 3


def test_drift_threshold_boundary_is_strict():
    exactly = make_snap(1, price=(1.0, 1.005))   # exactly 0.5%: not drift
    over = make_snap(2, price=(1.0, 1.0051))
    under = make_snap(3, price=(1.004, 1.0))
    d = compute_stats([exactly, over, under])["drift"]
    assert d["compared"] == 3
    assert d["rate"] == pytest.approx(1 / 3)
    assert compute_stats([exactly])["drift"]["rate"] == 0.0


def test_drift_uses_min_price_as_base_and_custom_threshold():
    d = compute_stats([make_snap(1, price=(2.0, 1.0))], drift_threshold=0.5)["drift"]
    assert d["rate"] == 1.0 and d["threshold"] == 0.5
    d = compute_stats([make_snap(1, price=(2.0, 1.0))], drift_threshold=1.0)["drift"]
    assert d["rate"] == 0.0  # |2-1|/1 == 1.0, not strictly greater


def test_error_snapshots_excluded_entirely():
    hist = [make_snap(1, buy=(200, 100)),
            make_snap(2, buy=(100, 200), status="error", ts=T0 + 10_000_000)]
    s = compute_stats(hist)
    assert s["snapshots"] == 1
    assert s["buy"]["wins"] == {"3000": 1, "10000": 0, "tie": 0}
    assert s["end_ts"] == T0 + 12  # the error snapshot does not move the window


def test_only_error_snapshots_gives_empty_stats():
    s = compute_stats([make_snap(1, status="error")])
    assert s["window"] is None and s["snapshots"] == 0


def test_window_cutoff_seven_days():
    day = 86400
    old = make_snap(1, ts=T0 - 7 * day - 1, buy=(1, 100))   # just outside
    edge = make_snap(2, ts=T0 - 7 * day, buy=(200, 100))    # exactly on cutoff: inside
    new = make_snap(3, ts=T0, buy=(200, 100))
    s = compute_stats([old, edge, new])
    assert s["snapshots"] == 2
    assert s["buy"]["wins"] == {"3000": 2, "10000": 0, "tie": 0}
    assert s["start_ts"] == T0 - 7 * day and s["end_ts"] == T0
    assert s["window"] == "2025-11-17/2025-11-24"
    assert compute_stats([old, edge, new], days=1)["snapshots"] == 1


def test_window_dates_are_utc():
    # 2025-11-24 23:59:59 UTC and 2025-11-25 00:00:00 UTC
    a, b = 1764028799, 1764028800
    s = compute_stats([make_snap(1, ts=a), make_snap(2, ts=b)])
    assert s["window"] == "2025-11-24/2025-11-25"


def test_unsorted_and_duplicate_blocks():
    hist = [make_snap(3, buy=(100, 100)),
            make_snap(1, buy=(200, 100)),
            make_snap(2, buy=(200, 100)),
            make_snap(2, buy=(100, 200))]  # duplicate block 2: last one kept
    b = compute_stats(hist)["buy"]
    assert b["compared"] == 3
    assert b["wins"] == {"3000": 1, "10000": 1, "tie": 1}


def test_fee_tiers_default_to_newest_snapshot_and_explicit_override():
    hist = [make_snap(1, fees=(500, 3000), buy=(200, 100)),
            make_snap(2, fees=(3000, 10000), buy=(200, 100))]
    s = compute_stats(hist)
    assert list(s["buy"]["wins"]) == ["3000", "10000", "tie"]
    assert s["buy"]["compared"] == 1  # snapshot 1 lacks the 10000 pool
    s = compute_stats(hist, fee_tiers=[500, 3000])
    assert s["buy"]["compared"] == 1 and s["buy"]["wins"]["500"] == 1


def test_bad_fee_tiers_length():
    with pytest.raises(ValueError):
        compute_stats([make_snap(1)], fee_tiers=[3000])


def test_big_ints_beyond_float_precision():
    base = 10**30
    # These collapse to the same float but differ by 1 wei.
    assert float(base) == float(base + 1)
    s = compute_stats([make_snap(1, buy=(base + 1, base))])["buy"]
    assert s["wins"] == {"3000": 1, "10000": 0, "tie": 0}
    assert s["max_advantage"] == float(Fraction(1, base))
    s = compute_stats([make_snap(1, buy=(base, base))])["buy"]
    assert s["wins"]["tie"] == 1


def test_zero_loser_counts_win_without_advantage():
    s = compute_stats([make_snap(1, buy=(5, 0))])["buy"]
    assert s["wins"]["3000"] == 1 and s["max_advantage"] is None


def test_rates_plus_tie_share_sum_to_one():
    hypothesis = pytest.importorskip("hypothesis")
    st = hypothesis.strategies

    @hypothesis.given(st.lists(st.tuples(st.integers(0, 10**25), st.integers(0, 10**25)),
                               min_size=1, max_size=30))
    def prop(pairs):
        hist = [make_snap(i + 1, buy=p) for i, p in enumerate(pairs)]
        b = compute_stats(hist)["buy"]
        assert b["compared"] == len(pairs)
        total = sum(b["win_rate"].values()) + b["wins"]["tie"] / b["compared"]
        assert total == pytest.approx(1.0)

    prop()
