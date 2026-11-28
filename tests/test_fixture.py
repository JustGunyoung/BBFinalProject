"""Offline tests against the recorded real-chain fixture in tests/fixtures/.

The fixture holds every RPC answer for one pinned block, so these tests need no
API key. Re-record it with scripts/record_fixture.py.
"""
import json
from fractions import Fraction
from pathlib import Path

import pytest

from tracker import config
from tracker.chain import load_pools, snapshot
from tracker.pricing import check_tick, to_price
from tracker.rpc import FixtureRpc

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_FILES = sorted(p for p in FIXTURES.glob("block_*.json") if not p.name.endswith(".expected.json"))

pytestmark = pytest.mark.skipif(not FIXTURE_FILES, reason="no recorded fixture in tests/fixtures/")

POOL_ADDRESSES = {
    500: "0x5d4F3C6fA16908609BAC31Ff148Bd002AA6b8c83",
    3000: "0xa6Cc3C2531FdaA6Ae1A3CA84c2855806728693e8",
}


@pytest.fixture(scope="module")
def fixture_path():
    return FIXTURE_FILES[-1]


@pytest.fixture(scope="module")
def expected(fixture_path):
    path = fixture_path.with_name(fixture_path.stem + ".expected.json")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snap(fixture_path):
    rpc = FixtureRpc(fixture_path)
    block = rpc.latest_block_number()
    market = load_pools(rpc, block)
    return snapshot(rpc, market.pools, block, market.oracle_decimals)


def test_snapshot_equals_expected(snap, expected):
    assert snap == expected


def test_pools_are_the_configured_tiers(snap):
    assert [p["fee"] for p in snap["pools"]] == list(config.FEE_TIERS) == [500, 3000]
    assert {p["fee"]: p["pool"] for p in snap["pools"]} == POOL_ADDRESSES


def test_recorded_sqrt_price_gives_expected_price(snap):
    for pool in snap["pools"]:
        exact = to_price(int(pool["sqrt_price_x96"]), 18, 18)
        assert pool["price_weth_per_link"] == pytest.approx(float(exact), rel=1e-12)
        assert 0.004 < pool["price_weth_per_link"] < 0.008  # about 0.0055 WETH per LINK
    # Hand check on the 500 pool: (sqrtP / 2^96)^2
    p500 = snap["pools"][0]
    assert to_price(int(p500["sqrt_price_x96"]), 18, 18) == Fraction(int(p500["sqrt_price_x96"]), 2**96) ** 2


def test_pools_agree_with_each_other(snap):
    a, b = (p["price_weth_per_link"] for p in snap["pools"])
    gap = abs(a - b) / min(a, b)
    assert 0.0005 < gap < 0.05  # they differ a little, but not wildly


def test_pools_agree_with_chainlink(snap):
    oracle = snap["oracle_raw"]
    price = int(oracle["answer"]) / 10 ** oracle["decimals"]
    for pool in snap["pools"]:
        assert abs(pool["price_weth_per_link"] / price - 1) < 0.05


def test_tick_check_passes(snap):
    for pool in snap["pools"]:
        assert pool["price_check"] == "ok"
        assert check_tick(int(pool["sqrt_price_x96"]), pool["tick"])


def test_quotes_are_non_zero(snap):
    for pool in snap["pools"]:
        assert int(pool["quote_buy_link_out"]) > 0
        assert int(pool["quote_sell_weth_out"]) > 0
        assert int(pool["liquidity"]) > 0
