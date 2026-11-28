"""Startup validation, multicall decoding and backfill, all against FakeRpc."""

import pytest

from tracker import abi, config
from tracker.chain import (
    PoolCheckFailed,
    backfill,
    build_multicall,
    decode_multicall,
    load_pools,
    snapshot,
)
from tracker.rpc import RpcError
from tests.fakes import (
    FAKE_POOLS,
    build_fake_chain,
    encode_result,
    fake_results,
    fake_state,
)

BLOCK = 23_000_100
INFO = {"number": BLOCK, "timestamp": 1_764_000_000, "base_fee": 8_500_000_000}


def test_load_pools_happy_path():
    market = load_pools(build_fake_chain([BLOCK]), BLOCK)
    assert [p.fee for p in market.pools] == list(config.FEE_TIERS)
    assert market.pools == FAKE_POOLS
    assert market.oracle_decimals == 18


def test_wrong_chain():
    rpc = build_fake_chain([BLOCK])
    rpc._chain_id = 5
    with pytest.raises(PoolCheckFailed, match="chain"):
        load_pools(rpc, BLOCK)


def test_zero_address_pool():
    rpc = build_fake_chain([BLOCK])
    data = abi.GET_POOL.encode(config.LINK, config.WETH, FAKE_POOLS[0].fee)
    rpc.set_call(config.FACTORY, data, encode_result(abi.GET_POOL, config.ZERO_ADDRESS))
    with pytest.raises(PoolCheckFailed, match="no LINK/WETH pool"):
        load_pools(rpc, BLOCK)


@pytest.mark.parametrize(
    "fn, value, message",
    [
        (abi.POOL_FEE, 1, "fee"),  # no tier uses fee 1
        (abi.POOL_TICK_SPACING, 1, "tick spacing"),  # no tier uses spacing 1
        (abi.POOL_TOKEN0, config.WETH, "pair"),
        (abi.POOL_TOKEN1, config.LINK, "pair"),
        (abi.POOL_FACTORY, config.QUOTER_V2, "factory"),
    ],
)
def test_wrong_pool_fields(fn, value, message):
    rpc = build_fake_chain([BLOCK])
    rpc.set_call(FAKE_POOLS[0].address, fn.encode(), encode_result(fn, value))
    with pytest.raises(PoolCheckFailed, match=message):
        load_pools(rpc, BLOCK)


def test_wrong_symbol_decimals_and_oracle():
    for fn, value in ((abi.ERC20_SYMBOL, "LNK"), (abi.ERC20_DECIMALS, 6)):
        rpc = build_fake_chain([BLOCK])
        rpc.set_call(config.LINK, fn.encode(), encode_result(fn, value))
        with pytest.raises(PoolCheckFailed, match="unexpected token"):
            load_pools(rpc, BLOCK)
    rpc = build_fake_chain([BLOCK])
    fn = abi.CHAINLINK_DESCRIPTION
    rpc.set_call(config.CHAINLINK_LINK_ETH, fn.encode(), encode_result(fn, "UNI / ETH"))
    with pytest.raises(PoolCheckFailed, match="oracle"):
        load_pools(rpc, BLOCK)


def test_build_multicall_order():
    calls = build_multicall(FAKE_POOLS)
    assert len(calls) == 9
    assert all(allow_failure for _, allow_failure, _ in calls)
    targets = [t for t, _, _ in calls]
    a, b = FAKE_POOLS[0].address, FAKE_POOLS[1].address
    q = config.QUOTER_V2
    assert targets == [a, a, q, q, b, b, q, q, config.CHAINLINK_LINK_ETH]
    assert calls[0][2] == abi.POOL_SLOT0.selector
    buy = abi.QUOTE_EXACT_INPUT_SINGLE.encode(
        (config.WETH, config.LINK, config.BUY_SIZE_WETH, FAKE_POOLS[0].fee, 0)
    )
    assert calls[2][2] == buy


def test_decode_happy_path():
    snap = decode_multicall(FAKE_POOLS, fake_results(BLOCK), INFO)
    assert snap["schema_version"] == 1
    assert snap["block"] == BLOCK and snap["rpc_status"] == "ok"
    assert snap["base_fee_wei"] == "8500000000"
    assert snap["buy_size_weth"] == str(config.BUY_SIZE_WETH)
    assert snap["sell_size_link"] == str(config.SELL_SIZE_LINK)
    assert [p["fee"] for p in snap["pools"]] == list(config.FEE_TIERS)
    p0 = snap["pools"][0]
    state = fake_state(BLOCK, FAKE_POOLS[0])
    assert p0["price_check"] == "ok"
    assert p0["price_weth_per_link"] == pytest.approx(0.004 * (1 + 0.0001 * (BLOCK % 50)))
    assert p0["sqrt_price_x96"] == str(state["sqrt"])
    assert p0["tick"] == state["tick"]
    assert p0["quote_buy_link_out"] == str(state["buy_out"])
    assert p0["quote_sell_weth_out"] == str(state["sell_out"])
    oracle = snap["oracle_raw"]
    assert oracle["answer"] == "4000000000000000" and oracle["decimals"] == 18
    assert oracle["round_id"] == str(2**80 - 5)


def test_big_integers_are_strings():
    snap = decode_multicall(FAKE_POOLS, fake_results(BLOCK), INFO)
    for pool in snap["pools"]:
        for key in ("sqrt_price_x96", "liquidity", "quote_buy_link_out", "quote_sell_weth_out"):
            assert isinstance(pool[key], str)
    assert isinstance(snap["base_fee_wei"], str)
    assert isinstance(snap["oracle_raw"]["round_id"], str)
    assert isinstance(snap["oracle_raw"]["answer"], str)


def test_failed_subcall_only_nulls_that_field():
    good = decode_multicall(FAKE_POOLS, fake_results(BLOCK), INFO)
    results = fake_results(BLOCK)
    results[1] = (False, b"")  # pool 0 liquidity reverted
    snap = decode_multicall(FAKE_POOLS, results, INFO)
    assert snap["pools"][0]["liquidity"] is None
    assert snap["pools"][0] == dict(good["pools"][0], liquidity=None)
    assert snap["pools"][1] == good["pools"][1]
    assert snap["oracle_raw"] == good["oracle_raw"]


def test_undecodable_subcall_counts_as_failed():
    results = fake_results(BLOCK)
    results[3] = (True, b"\x01\x02")  # garbage sell quote on pool 0
    snap = decode_multicall(FAKE_POOLS, results, INFO)
    assert snap["pools"][0]["quote_sell_weth_out"] is None
    assert snap["pools"][0]["quote_buy_link_out"] is not None


def test_failed_slot0_and_oracle():
    results = fake_results(BLOCK)
    results[4] = (False, b"")  # pool 1 slot0
    results[8] = (False, b"")  # oracle
    snap = decode_multicall(FAKE_POOLS, results, INFO)
    p1 = snap["pools"][1]
    assert p1["price_check"] == "missing"
    assert p1["sqrt_price_x96"] is None and p1["tick"] is None
    assert p1["price_weth_per_link"] is None
    assert p1["liquidity"] is not None and p1["quote_buy_link_out"] is not None
    assert snap["oracle_raw"] is None


def test_sqrt_and_tick_disagreement_marks_price_check_failed():
    state = fake_state(BLOCK, FAKE_POOLS[0])
    results = fake_results(BLOCK)
    results[0] = (
        True,
        encode_result(abi.POOL_SLOT0, state["sqrt"], state["tick"] + 500, 0, 1, 1, 0, True),
    )
    p0 = decode_multicall(FAKE_POOLS, results, INFO)["pools"][0]
    assert p0["price_check"] == "failed"
    assert p0["price_weth_per_link"] is None
    assert p0["sqrt_price_x96"] == str(state["sqrt"])
    assert p0["tick"] == state["tick"] + 500


def test_wrong_result_count():
    with pytest.raises(ValueError):
        decode_multicall(FAKE_POOLS, fake_results(BLOCK)[:-1], INFO)


def test_snapshot_uses_one_block_read_and_one_call():
    rpc = build_fake_chain([BLOCK])
    snap = snapshot(rpc, FAKE_POOLS, BLOCK)
    assert snap["block"] == BLOCK
    assert rpc.calls["get_block"] == 1 and rpc.calls["eth_call"] == 1
    assert rpc.eth_call_log[0][0] == config.MULTICALL3 and rpc.eth_call_log[0][2] == BLOCK


def test_backfill_visits_expected_blocks():
    blocks = list(range(100, 201, 25))
    rpc = build_fake_chain(blocks)
    seen, sleeps = [], []
    backfill(rpc, FAKE_POOLS, 100, 200, 25, lambda s: seen.append(s["block"]), 0.2, sleeps.append)
    assert seen == [100, 125, 150, 175, 200]
    assert sleeps == [0.2] * 5


def test_backfill_retries_on_rate_limit():
    rpc = build_fake_chain([100, 125])
    seen, sleeps = [], []
    rpc.fail_next(2)  # the first block fails twice with a 429
    backfill(rpc, FAKE_POOLS, 100, 125, 25, lambda s: seen.append(s["block"]), 0.2, sleeps.append)
    assert seen == [100, 125]
    assert sleeps == [2, 4, 0.2, 0.2]


def test_backfill_gives_up_after_three_retries():
    rpc = build_fake_chain([100])
    sleeps = []
    rpc.fail_next(4)
    with pytest.raises(RpcError) as info:
        backfill(rpc, FAKE_POOLS, 100, 100, 25, lambda s: None, 0.2, sleeps.append)
    assert info.value.rate_limited
    assert sleeps == [2, 4, 8]


def test_backfill_does_not_retry_other_errors():
    rpc = build_fake_chain([100])
    sleeps = []
    rpc.fail_next(1, "connection refused")
    with pytest.raises(RpcError):
        backfill(rpc, FAKE_POOLS, 100, 100, 25, lambda s: None, 0.2, sleeps.append)
    assert sleeps == []
