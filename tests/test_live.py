import os

import pytest

from tracker import config
from tracker.chain import load_pools, snapshot
from tracker.rpc import Rpc

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("API_KEY"), reason="API_KEY not set"),
]


def test_live_snapshot():
    rpc = Rpc.from_env()
    latest = rpc.latest_block_number()
    market = load_pools(rpc, latest)
    snap = snapshot(rpc, market.pools, latest, market.oracle_decimals)
    assert len(snap["pools"]) == len(config.FEE_TIERS)
    for pool in snap["pools"]:
        assert pool["price_weth_per_link"] > 0
        assert pool["quote_buy_link_out"] is not None
        assert pool["quote_sell_weth_out"] is not None
