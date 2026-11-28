"""Pre-flight checks for the LINK/WETH tracker: python scripts/preflight.py

Runs four independent checks against the real chain (fee tiers, archive depth,
quoter, oracle), prints a report and a summary of what to put in config.py.
Exit code is non-zero if any check fails.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import abi, config  # noqa: E402
from tracker.pricing import to_price  # noqa: E402
from tracker.rpc import Rpc, RpcError  # noqa: E402

ONE = 10**18


def hr(n: int) -> str:
    return f"{n / ONE:.6f}"


def call(rpc, to, fn, args, block):
    return fn.decode(rpc.eth_call(to, fn.encode(*args), block))


def quote(rpc, fee, token_in, token_out, amount, block):
    params = (token_in, token_out, amount, fee, 0)
    return call(rpc, config.QUOTER_V2, abi.QUOTE_EXACT_INPUT_SINGLE, (params,), block)[0]


def nice_round(x: float) -> int:
    """Round to 1 significant digit steps like 1, 2, 5, 10, 20, 50, 100."""
    if x <= 0:
        return 1
    import math
    mag = 10 ** math.floor(math.log10(x))
    return int(min((m * mag for m in (1, 2, 5, 10)), key=lambda c: abs(c - x)) or 1)


def check_tiers(rpc, ctx):
    print("\n[1] Fee tiers")
    pools = {}
    for fee in config.CANDIDATE_FEE_TIERS:
        (addr,) = call(rpc, config.FACTORY, abi.GET_POOL, (config.LINK, config.WETH, fee), ctx["latest"])
        if addr == config.ZERO_ADDRESS:
            print(f"  fee {fee:>6}: no pool")
            continue
        (liq,) = call(rpc, addr, abi.POOL_LIQUIDITY, (), ctx["latest"])
        slot0 = call(rpc, addr, abi.POOL_SLOT0, (), ctx["latest"])
        price = float(to_price(slot0[0], 18, 18))
        pools[fee] = {"address": addr, "liquidity": liq, "price": price}
    ctx["pools"] = pools
    ranked = sorted(pools.items(), key=lambda kv: -kv[1]["liquidity"])
    print(f"  {'fee':>6}  {'pool':<44}{'liquidity':>26}  price (WETH/LINK)")
    for fee, p in ranked:
        print(f"  {fee:>6}  {p['address']:<44}{p['liquidity']:>26}  {p['price']:.8f}")
    if len(ranked) < 2:
        raise RuntimeError("fewer than two LINK/WETH pools exist")
    top = sorted(fee for fee, _ in ranked[:2])
    ctx["fee_tiers"] = tuple(top)
    ctx["deepest"] = ranked[0][0]
    print(f"  Recommended FEE_TIERS = ({top[0]}, {top[1]})  (by raw liquidity; see quotes in [3])")
    lo, hi = top
    ratio = pools[lo]["liquidity"] / max(pools[hi]["liquidity"], 1)
    if ratio > 10:
        print(f"  WARNING: the {lo} pool has {ratio:.1f}x the liquidity of the {hi} pool,"
              " so the comparison may be one-sided (the plan's UNI/WETH note).")
    print("  Note: raw liquidity is only roughly comparable across tiers (concentrated liquidity).")


def check_archive(rpc, ctx):
    print("\n[2] Archive depth")
    pool = ctx["pools"][ctx["deepest"]]["address"]
    for label, back in (("7 days", 7 * config.BLOCKS_PER_DAY), ("3 days", 3 * config.BLOCKS_PER_DAY),
                        ("1 day", config.BLOCKS_PER_DAY), ("1 hour", 300)):
        block = ctx["latest"] - back
        try:
            call(rpc, pool, abi.POOL_SLOT0, (), block)
        except RpcError as exc:
            print(f"  {label} back (block {block}): FAILED ({str(exc)[:80]})")
            continue
        print(f"  {label} back (block {block}): OK")
        ctx["past"] = block
        ctx["archive"] = label
        if label != "7 days":
            raise RuntimeError(f"archive reaches only {label}; use --days accordingly")
        return
    raise RuntimeError("no archive access even 1 hour back")


def check_quoter(rpc, ctx):
    print("\n[3] Quoter (individual eth_calls)")
    blocks = [("latest", ctx["latest"])]
    if "past" in ctx:
        blocks.append((f"past ({ctx['archive']})", ctx["past"]))
    print(f"  buy {hr(config.BUY_SIZE_WETH)} WETH -> LINK, sell {hr(config.SELL_SIZE_LINK)} LINK -> WETH")
    sells = {}
    for fee, p in ctx["pools"].items():
        for label, block in blocks:
            buy = quote(rpc, fee, config.WETH, config.LINK, config.BUY_SIZE_WETH, block)
            sell = quote(rpc, fee, config.LINK, config.WETH, config.SELL_SIZE_LINK, block)
            print(f"  fee {fee:>6} @ {label:<16} buy -> {hr(buy)} LINK   sell -> {hr(sell)} WETH")
            if block == ctx["latest"]:
                sells[fee] = (buy, sell)
    ctx["quotes"] = sells
    fee0 = ctx["deepest"]
    value = config.SELL_SIZE_LINK / ONE * ctx["pools"][fee0]["price"]
    want = config.BUY_SIZE_WETH / ONE
    link_amount = want / ctx["pools"][fee0]["price"]
    ctx["suggest_sell"] = nice_round(link_amount)
    print(f"  SELL_SIZE_LINK is worth {value:.6f} WETH at spot (BUY_SIZE_WETH = {want:.6f}).")
    print(f"  A LINK size matching BUY_SIZE_WETH in value: about {link_amount:.2f}"
          f" -> suggest SELL_SIZE_LINK = {ctx['suggest_sell']} * 10**18")


def check_oracle(rpc, ctx):
    print("\n[4] Chainlink oracle")
    feed = config.CHAINLINK_LINK_ETH
    description = call(rpc, feed, abi.CHAINLINK_DESCRIPTION, (), ctx["latest"])[0]
    decimals = call(rpc, feed, abi.CHAINLINK_DECIMALS, (), ctx["latest"])[0]
    _, answer, _, updated_at, _ = call(rpc, feed, abi.CHAINLINK_LATEST_ROUND_DATA, (), ctx["latest"])
    print(f"  description = {description!r}, decimals = {decimals}")
    ok = description == config.CHAINLINK_DESCRIPTION and decimals == config.CHAINLINK_DECIMALS
    price = answer / 10**decimals
    block_ts = rpc.block(ctx["latest"])["timestamp"]
    print(f"  answer = {price:.8f} WETH per LINK, updated {(block_ts - updated_at) / 60:.1f} min before the latest block")
    for fee, p in ctx["pools"].items():
        print(f"  pool {fee:>6} spot {p['price']:.8f} vs oracle: {(p['price'] / price - 1) * 100:+.3f}%")
    if not ok:
        raise RuntimeError(f"FAIL: expected {config.CHAINLINK_DESCRIPTION!r} with {config.CHAINLINK_DECIMALS} decimals")


def main(argv=None) -> int:
    argparse.ArgumentParser(description="Run the four pre-flight checks against the real chain "
                                        "(needs API_KEY in .env).").parse_args(argv)
    try:
        rpc = Rpc.from_env()
        latest = rpc.latest_block_number()
    except RpcError as exc:
        print(exc)
        return 1
    ctx = {"latest": latest}
    print(f"Latest block: {latest}")
    failures = []
    for name, check in (("tiers", check_tiers), ("archive", check_archive),
                        ("quoter", check_quoter), ("oracle", check_oracle)):
        try:
            check(rpc, ctx)
            print(f"  -> {name}: OK")
        except (RpcError, RuntimeError, KeyError) as exc:
            failures.append(name)
            print(f"  -> {name}: FAIL: {str(exc)[:200]!s}")

    print("\nSummary for config.py")
    if "fee_tiers" in ctx:
        print(f"  FEE_TIERS = {ctx['fee_tiers']}")
    if "suggest_sell" in ctx:
        print(f"  SELL_SIZE_LINK = {ctx['suggest_sell']} * 10**18   # currently {config.SELL_SIZE_LINK // ONE}")
    if "archive" in ctx:
        print(f"  archive reaches back {ctx['archive']}" + ("" if ctx["archive"] == "7 days" else "  (set HISTORY_DAYS lower)"))
    cu = sum(n * config.CU_COSTS.get(m, 0) for m, n in rpc.calls.items())
    print(f"  RPC calls: {dict(rpc.calls)} -> about {cu} CU")
    print("RESULT: " + ("ALL OK" if not failures else "FAIL (" + ", ".join(failures) + ")"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
