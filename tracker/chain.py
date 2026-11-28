"""Read and validate the two LINK/WETH pools, then take snapshots of them.

Startup (load_pools) reads each pool once with individual calls and runs the
Milestone 6 checks. After that, every snapshot is one Multicall3 call at one
pinned block: per pool slot0, liquidity and two quotes, then the Chainlink feed.
Each sub-call is decoded on its own, so one failure only blanks that one value.
"""

import time
from dataclasses import dataclass

from tracker import abi, config
from tracker.pricing import check_tick, to_price
from tracker.rpc import RpcError

CALLS_PER_POOL = 4  # slot0, liquidity, buy quote, sell quote
BACKFILL_RETRY_DELAYS = (2, 4, 8)  # seconds to wait after each 429


class PoolCheckFailed(Exception):
    """The chain data did not match what this code expects."""


def require(condition, message):
    if not condition:
        raise PoolCheckFailed(message)


@dataclass(frozen=True)
class PoolInfo:
    fee: int
    address: str
    tick_spacing: int
    dec0: int
    dec1: int


@dataclass(frozen=True)
class Market:
    """What startup learned: the pools (ordered as config.FEE_TIERS) and the
    oracle's decimals."""

    pools: list
    oracle_decimals: int


def _read(rpc, to, fn, args, block):
    """One individual eth_call, decoded. Bad data counts as a failed check."""
    data = rpc.eth_call(to, fn.encode(*args), block)
    try:
        return fn.decode(data)
    except Exception as exc:
        raise PoolCheckFailed(f"cannot decode {fn.name}() from {to}: {exc}") from None


def _read_token(rpc, address, block):
    (symbol,) = _read(rpc, address, abi.ERC20_SYMBOL, (), block)
    (decimals,) = _read(rpc, address, abi.ERC20_DECIMALS, (), block)
    return symbol, decimals


def load_pools(rpc, block: int) -> Market:
    """Find both pools through the factory and validate them at `block`.

    Raises PoolCheckFailed if anything is not what config says. Also checks
    the Chainlink feed's description and decimals and returns its decimals.
    """
    chain_id = rpc.chain_id()
    require(chain_id == config.CHAIN_ID, f"expected chain {config.CHAIN_ID}, RPC is on {chain_id}")

    pools = []
    for fee in config.FEE_TIERS:
        (address,) = _read(rpc, config.FACTORY, abi.GET_POOL, (config.LINK, config.WETH, fee), block)
        require(
            address != config.ZERO_ADDRESS,
            f"factory has no LINK/WETH pool at fee tier {fee}",
        )

        (pool_factory,) = _read(rpc, address, abi.POOL_FACTORY, (), block)
        (token0,) = _read(rpc, address, abi.POOL_TOKEN0, (), block)
        (token1,) = _read(rpc, address, abi.POOL_TOKEN1, (), block)
        (pool_fee,) = _read(rpc, address, abi.POOL_FEE, (), block)
        (tick_spacing,) = _read(rpc, address, abi.POOL_TICK_SPACING, (), block)
        symbol0, dec0 = _read_token(rpc, token0, block)
        symbol1, dec1 = _read_token(rpc, token1, block)

        # Validation: each check catches a different way to read the wrong thing.
        require(pool_factory == config.FACTORY, f"pool reports factory {pool_factory}")
        require(
            (token0, token1) == (config.LINK, config.WETH),
            f"unexpected pair {token0} / {token1}",
        )
        require(pool_fee == fee, f"expected fee {fee}, pool reports {pool_fee}")
        expected_spacing = config.TICK_SPACINGS[fee]
        require(
            tick_spacing == expected_spacing,
            f"expected tick spacing {expected_spacing}, pool reports {tick_spacing}",
        )
        for symbol, dec, expected in ((symbol0, dec0, config.TOKEN0), (symbol1, dec1, config.TOKEN1)):
            require(
                (symbol, dec) == (expected["symbol"], expected["decimals"]),
                f"unexpected token {symbol} with {dec} decimals",
            )
        pools.append(PoolInfo(fee, address, tick_spacing, dec0, dec1))

    (description,) = _read(rpc, config.CHAINLINK_LINK_ETH, abi.CHAINLINK_DESCRIPTION, (), block)
    (oracle_decimals,) = _read(rpc, config.CHAINLINK_LINK_ETH, abi.CHAINLINK_DECIMALS, (), block)
    require(
        description == config.CHAINLINK_DESCRIPTION,
        f"unexpected oracle feed {description!r}",
    )
    require(
        oracle_decimals == config.CHAINLINK_DECIMALS,
        f"expected oracle decimals {config.CHAINLINK_DECIMALS}, feed reports {oracle_decimals}",
    )
    return Market(pools, oracle_decimals)


def build_multicall(pools) -> list:
    """The aggregate3 calls as (target, allowFailure, callData) tuples.

    Fixed order: for each pool [slot0, liquidity, buy quote, sell quote],
    then the oracle's latestRoundData. Nine calls for two pools.
    """
    calls = []
    for pool in pools:
        # Buy LINK: WETH in. Sell LINK: LINK in. Price limit 0 means no limit.
        buy = (config.WETH, config.LINK, config.BUY_SIZE_WETH, pool.fee, 0)
        sell = (config.LINK, config.WETH, config.SELL_SIZE_LINK, pool.fee, 0)
        calls += [
            (pool.address, True, abi.POOL_SLOT0.encode()),
            (pool.address, True, abi.POOL_LIQUIDITY.encode()),
            (config.QUOTER_V2, True, abi.QUOTE_EXACT_INPUT_SINGLE.encode(buy)),
            (config.QUOTER_V2, True, abi.QUOTE_EXACT_INPUT_SINGLE.encode(sell)),
        ]
    calls.append((config.CHAINLINK_LINK_ETH, True, abi.CHAINLINK_LATEST_ROUND_DATA.encode()))
    return calls


def _decode(fn, result):
    """Decode one aggregate3 result, or None if the sub-call failed or is garbage."""
    success, data = result
    if not success:
        return None
    try:
        return fn.decode(data)
    except Exception:
        return None


def _decode_pool(pool, results) -> dict:
    slot0 = _decode(abi.POOL_SLOT0, results[0])
    liquidity = _decode(abi.POOL_LIQUIDITY, results[1])
    buy = _decode(abi.QUOTE_EXACT_INPUT_SINGLE, results[2])
    sell = _decode(abi.QUOTE_EXACT_INPUT_SINGLE, results[3])

    sqrt_price = slot0[0] if slot0 else None
    tick = slot0[1] if slot0 else None
    price = None
    if slot0 is None:
        price_check = "missing"
    elif check_tick(sqrt_price, tick):
        price_check = "ok"
        price = float(to_price(sqrt_price, pool.dec0, pool.dec1))
    else:
        price_check = "failed"  # keep sqrt and tick for inspection, but no price

    return {
        "fee": pool.fee,
        "pool": pool.address,
        "sqrt_price_x96": None if sqrt_price is None else str(sqrt_price),
        "tick": tick,
        "liquidity": str(liquidity[0]) if liquidity else None,
        "price_weth_per_link": price,
        "price_check": price_check,
        "quote_buy_link_out": str(buy[0]) if buy else None,
        "quote_sell_weth_out": str(sell[0]) if sell else None,
    }


def decode_multicall(pools, results, block_info, oracle_decimals=config.CHAINLINK_DECIMALS) -> dict:
    """Turn aggregate3 results into a snapshot dict (without stats).

    `results` is a list of (success, returnData) in build_multicall order and
    `block_info` is what Rpc.block() returns.
    """
    expected = CALLS_PER_POOL * len(pools) + 1
    if len(results) != expected:
        raise ValueError(f"expected {expected} multicall results, got {len(results)}")

    pool_snaps = [
        _decode_pool(pool, results[i * CALLS_PER_POOL : (i + 1) * CALLS_PER_POOL])
        for i, pool in enumerate(pools)
    ]

    round_data = _decode(abi.CHAINLINK_LATEST_ROUND_DATA, results[-1])
    oracle_raw = None
    if round_data:
        round_id, answer, started_at, updated_at, answered_in_round = round_data
        oracle_raw = {
            "round_id": str(round_id),
            "answer": str(answer),
            "started_at": started_at,
            "updated_at": updated_at,
            "answered_in_round": str(answered_in_round),
            "decimals": oracle_decimals,
        }

    base_fee = block_info.get("base_fee")
    return {
        "schema_version": config.SCHEMA_VERSION,
        "block": block_info["number"],
        "timestamp": block_info["timestamp"],
        "base_fee_wei": None if base_fee is None else str(base_fee),
        "rpc_status": "ok",
        "buy_size_weth": str(config.BUY_SIZE_WETH),
        "sell_size_link": str(config.SELL_SIZE_LINK),
        "pools": pool_snaps,
        "oracle_raw": oracle_raw,
    }


def snapshot(rpc, pools, block: int, oracle_decimals=config.CHAINLINK_DECIMALS) -> dict:
    """One snapshot at `block`: one block read plus one Multicall3 eth_call."""
    info = rpc.block(block)
    data = abi.AGGREGATE3.encode(build_multicall(pools))
    raw = rpc.eth_call(config.MULTICALL3, data, block)
    try:
        (results,) = abi.AGGREGATE3.decode(raw)
    except Exception as exc:
        raise RpcError(f"undecodable multicall response at block {block}: {exc}") from None
    return decode_multicall(pools, results, info, oracle_decimals)


def _snapshot_with_backoff(rpc, pools, block, oracle_decimals, sleep):
    for delay in (*BACKFILL_RETRY_DELAYS, None):
        try:
            return snapshot(rpc, pools, block, oracle_decimals)
        except RpcError as exc:
            if not exc.rate_limited or delay is None:
                raise
            sleep(delay)  # rate limited: wait, then try the same block again


def backfill(
    rpc,
    pools,
    start: int,
    end: int,
    step: int,
    on_snapshot,
    pause: float = config.BACKFILL_PAUSE,
    sleep=time.sleep,
    oracle_decimals=config.CHAINLINK_DECIMALS,
) -> None:
    """Snapshot every `step`-th block from start to end (inclusive).

    Calls on_snapshot(snap) for each one so the caller can save progress, and
    pauses between blocks. A 429 is retried after 2, 4 and 8 seconds; after
    that, or on any other RpcError, the error propagates.
    """
    for block in range(start, end + 1, step):
        on_snapshot(_snapshot_with_backoff(rpc, pools, block, oracle_decimals, sleep))
        sleep(pause)
