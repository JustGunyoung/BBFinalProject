"""Offline stand-ins for the chain, shared by the chain and poller tests.

FakeRpc has the same methods as tracker.rpc.Rpc but answers from a dict of
canned eth_call results. build_fake_chain() fills one with a complete valid
LINK/WETH world: two pools, quotes and an oracle for every block you list.

    rpc = build_fake_chain([100, 125, 150])
    market = load_pools(rpc, 150)
    snap = snapshot(rpc, market.pools, 125)

To break something, change a sub-result with fake_results() and re-register it
with set_multicall(), or queue errors with rpc.fail_next(...).
"""

import math
from collections import Counter

from web3 import Web3

from tracker import abi, config
from tracker.chain import PoolInfo, build_multicall
from tracker.pricing import price_to_sqrt_price_x96, raw_price
from tracker.rpc import RpcError

BASE_BLOCK = 23_000_000
BASE_TIMESTAMP = 1_764_000_000
FAKE_BASE_FEE = 8_500_000_000

# Made-up pool addresses (checksummed) for every candidate tier, so the fakes
# follow whichever two tiers config.FEE_TIERS picks.
FAKE_POOL_ADDRESSES = {
    500: Web3.to_checksum_address("0x" + "a5" * 20),
    3000: Web3.to_checksum_address("0x" + "a3" * 20),
    10000: Web3.to_checksum_address("0x" + "a1" * 20),
}
FAKE_POOLS = [
    PoolInfo(fee, FAKE_POOL_ADDRESSES[fee], config.TICK_SPACINGS[fee], 18, 18)
    for fee in config.FEE_TIERS
]


def encode_result(fn, *values) -> bytes:
    """ABI-encode the return value of `fn` (an abi.Fn)."""
    return fn.encode_result(*values)


def encode_multicall_result(results) -> bytes:
    """Encode a list of (success, returnData) as an aggregate3 return value."""
    return abi.AGGREGATE3.encode_result(list(results))


class FakeRpc:
    """Canned answers keyed by (to, calldata, block).

    set_call(..., block=None) answers at any block; a block-specific entry wins.
    A result of bytes is returned as is; a str is raised as RpcError.
    fail_next() queues errors that eth_call raises first, then answers normally.
    """

    def __init__(self, chain_id: int = config.CHAIN_ID, latest: int = BASE_BLOCK):
        self._chain_id = chain_id
        self.latest = latest
        self.blocks: dict = {}
        self.responses: dict = {}
        self.pending_errors: list = []
        self.calls: Counter = Counter()
        self.eth_call_log: list = []  # (to, data, block) of every eth_call

    def set_block(self, number: int, timestamp: int, base_fee: int = FAKE_BASE_FEE) -> None:
        self.blocks[number] = {"number": number, "timestamp": timestamp, "base_fee": base_fee}

    def set_call(self, to: str, data: bytes, result, block=None) -> None:
        self.responses[(to.lower(), bytes(data), block)] = result

    def fail_next(self, count: int, message: str = "429 Too Many Requests") -> None:
        self.pending_errors += [message] * count

    def chain_id(self) -> int:
        self.calls["chain_id"] += 1
        return self._chain_id

    def latest_block_number(self) -> int:
        self.calls["block_number"] += 1
        return self.latest

    def block(self, n: int) -> dict:
        self.calls["get_block"] += 1
        if n not in self.blocks:
            raise RpcError(f"no fake block {n}")
        return dict(self.blocks[n])

    def eth_call(self, to: str, data: bytes, block: int) -> bytes:
        self.calls["eth_call"] += 1
        self.eth_call_log.append((to, bytes(data), block))
        if self.pending_errors:
            raise RpcError(self.pending_errors.pop(0))
        key = (to.lower(), bytes(data))
        result = self.responses.get((*key, block), self.responses.get((*key, None)))
        if result is None:
            raise RpcError(f"no fake response for {to} at block {block}")
        if isinstance(result, str):
            raise RpcError(result)
        return result


def fake_state(block: int, pool: PoolInfo) -> dict:
    """Deterministic made-up market state for one pool at one block.

    The 1% pool is priced slightly higher and moves a little with the block
    number, so the two pools' quotes differ and change over time.
    """
    tier_bump = 1 + 0.002 * config.FEE_TIERS.index(pool.fee)
    price = 0.004 * tier_bump * (1 + 0.0001 * (block % 50))  # WETH per LINK
    sqrt = price_to_sqrt_price_x96(price, pool.dec0, pool.dec1)
    tick = math.floor(math.log(float(raw_price(sqrt))) / math.log(1.0001))
    keep = 1 - pool.fee / 1_000_000
    return {
        "sqrt": sqrt,
        "tick": tick,
        "liquidity": 10**21 + block,
        "buy_out": int(config.BUY_SIZE_WETH / price * keep),
        "sell_out": int(config.SELL_SIZE_LINK * price * keep),
    }


def fake_results(block: int, pools=FAKE_POOLS) -> list:
    """The nine (success, returnData) results for a block, in build_multicall order."""
    results = []
    for pool in pools:
        s = fake_state(block, pool)
        quote = lambda out: (True, encode_result(abi.QUOTE_EXACT_INPUT_SINGLE, out, s["sqrt"], 3, 150_000))
        results += [
            (True, encode_result(abi.POOL_SLOT0, s["sqrt"], s["tick"], 0, 1, 1, 0, True)),
            (True, encode_result(abi.POOL_LIQUIDITY, s["liquidity"])),
            quote(s["buy_out"]),
            quote(s["sell_out"]),
        ]
    answer = 4_000_000_000_000_000  # 0.004 ETH per LINK, 18 decimals
    timestamp = BASE_TIMESTAMP + 12 * (block - BASE_BLOCK)
    results.append(
        (True, encode_result(abi.CHAINLINK_LATEST_ROUND_DATA, 2**80 - 5, answer, timestamp - 600, timestamp - 600, 2**80 - 5))
    )
    return results


def set_multicall(rpc: FakeRpc, pools, block: int, results) -> None:
    """Make the aggregate3 call for `block` return `results`."""
    data = abi.AGGREGATE3.encode(build_multicall(pools))
    rpc.set_call(config.MULTICALL3, data, encode_multicall_result(results), block)


def build_fake_chain(blocks, pools=FAKE_POOLS) -> FakeRpc:
    """A complete valid fake chain: startup reads for load_pools plus a block
    entry and an aggregate3 answer for each block in `blocks`."""
    rpc = FakeRpc(latest=max(blocks))
    for pool in pools:
        args = (config.LINK, config.WETH, pool.fee)
        rpc.set_call(config.FACTORY, abi.GET_POOL.encode(*args), encode_result(abi.GET_POOL, pool.address))
        for fn, value in (
            (abi.POOL_FACTORY, config.FACTORY),
            (abi.POOL_TOKEN0, config.LINK),
            (abi.POOL_TOKEN1, config.WETH),
            (abi.POOL_FEE, pool.fee),
            (abi.POOL_TICK_SPACING, pool.tick_spacing),
        ):
            rpc.set_call(pool.address, fn.encode(), encode_result(fn, value))
    for token in (config.TOKEN0, config.TOKEN1):
        rpc.set_call(token["address"], abi.ERC20_SYMBOL.encode(), encode_result(abi.ERC20_SYMBOL, token["symbol"]))
        rpc.set_call(token["address"], abi.ERC20_DECIMALS.encode(), encode_result(abi.ERC20_DECIMALS, token["decimals"]))
    feed = config.CHAINLINK_LINK_ETH
    rpc.set_call(feed, abi.CHAINLINK_DESCRIPTION.encode(), encode_result(abi.CHAINLINK_DESCRIPTION, config.CHAINLINK_DESCRIPTION))
    rpc.set_call(feed, abi.CHAINLINK_DECIMALS.encode(), encode_result(abi.CHAINLINK_DECIMALS, config.CHAINLINK_DECIMALS))

    for block in blocks:
        rpc.set_block(block, BASE_TIMESTAMP + 12 * (block - BASE_BLOCK))
        set_multicall(rpc, pools, block, fake_results(block, pools))
    return rpc
