"""The poller: catch up on missing history, then snapshot every few minutes.

    python -m tracker.poller [--once] [--backfill] [--days N]

Each snapshot is appended to data/history.jsonl as soon as it is taken (that is
also the resume mechanism), and data/cache.json is rewritten after every poll.
"""

import argparse
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from tracker import chain, config
from tracker.chain import PoolCheckFailed, load_pools
from tracker.rpc import Rpc, RpcError
from tracker.stats import compute_stats
from tracker.store import append_history, read_history, write_cache

log = logging.getLogger("tracker.poller")

ONE = 10**18

# Module-level so tests can point them at a temp directory.
HISTORY_PATH = config.HISTORY_PATH
CACHE_PATH = config.CACHE_PATH
SEED_PATH = config.SEED_PATH


@dataclass
class PollerState:
    """Everything poll_once needs to remember between polls."""

    history_path: Path = config.HISTORY_PATH
    cache_path: Path = config.CACHE_PATH
    days: int = config.HISTORY_DAYS
    last_snapshot: dict | None = None
    last_stats: dict | None = None
    cu_total: int = 0
    prev_calls: dict = field(default_factory=dict)


def usable_history(history_path) -> list:
    return [s for s in read_history(history_path) if s.get("rpc_status") != "error"]


def catch_up(rpc, market, history_path, latest, days=config.HISTORY_DAYS,
             step=config.BACKFILL_STEP, sleep=time.sleep) -> int:
    """Fill history from the oldest allowed block up to just before `latest`.

    Blocks are oldest, oldest+step, ... while block <= latest - step, so the
    live poll at `latest` follows without a duplicate. Resumes after the last
    stored block (error lines ignored). Returns the number of snapshots added.
    """
    oldest = latest - days * config.BLOCKS_PER_DAY
    history = usable_history(history_path)
    start = oldest if not history else max(max(s["block"] for s in history) + step, oldest)
    end = latest - step
    if start > end:
        log.info("history is up to date, nothing to catch up")
        return 0
    total = (end - start) // step + 1
    log.info("catching up %d snapshots (blocks %d..%d)", total, start, start + (total - 1) * step)
    done = 0

    def on_snapshot(snap):
        nonlocal done
        append_history(history_path, snap)
        done += 1
        if done % 100 == 0:
            log.info("catch-up progress: %d/%d", done, total)

    chain.backfill(rpc, market.pools, start, end, step, on_snapshot,
                   pause=config.BACKFILL_PAUSE, sleep=sleep, oracle_decimals=market.oracle_decimals)
    return done


def catch_up_to_head(rpc, market, history_path, days, sleep=time.sleep, max_passes=5) -> int:
    """Run catch_up with a fresh head until a pass adds nothing (or max_passes).

    A long catch-up lets new blocks pass by; repeating closes that gap before
    the live poll. Returns the total number of snapshots added.
    """
    total = 0
    for n in range(1, max_passes + 1):
        if n > 1:
            log.info("catch-up pass %d: the chain moved on while catching up", n)
        added = catch_up(rpc, market, history_path, rpc.latest_block_number(), days, sleep=sleep)
        total += added
        if added == 0:
            break
    return total


def _stats(state, market):
    return compute_stats(usable_history(state.history_path), state.days, config.DRIFT_THRESHOLD,
                         [p.fee for p in market.pools])


def write_cache_from_history(market, state) -> dict | None:
    """Cache = newest stored snapshot + stats (used by --backfill)."""
    history = usable_history(state.history_path)
    if not history:
        log.warning("history is empty, cache not written")
        return None
    state.last_snapshot = history[-1]
    state.last_stats = _stats(state, market)
    write_cache(state.cache_path, {**state.last_snapshot, "stats": state.last_stats})
    return state.last_snapshot


def _spend(rpc, state) -> int:
    """CU used since the last call, from rpc.calls deltas."""
    now = dict(rpc.calls)
    cu = sum((n - state.prev_calls.get(m, 0)) * config.CU_COSTS.get(m, 0) for m, n in now.items())
    state.prev_calls = now
    state.cu_total += cu
    return cu


def _log_poll(snap, cu, state):
    def human(v):
        return "n/a" if v is None else f"{int(v) / ONE:.6f}"

    prices = ", ".join(
        f"{p['fee']}: {p['price_weth_per_link']:.8f}" if p["price_weth_per_link"] is not None
        else f"{p['fee']}: n/a" for p in snap["pools"])
    quotes = "; ".join(
        f"{p['fee']} buy {human(p['quote_buy_link_out'])} LINK, sell {human(p['quote_sell_weth_out'])} WETH"
        for p in snap["pools"])
    log.info("block %d | WETH/LINK %s | %s | %d CU (total %d)",
             snap["block"], prices, quotes, cu, state.cu_total)


def poll_once(rpc, market, state) -> dict:
    """Take one snapshot at the latest block, store it, refresh the cache.

    Raises RpcError if the chain could not be read (see safe_poll)."""
    block = rpc.latest_block_number()
    snap = chain.snapshot(rpc, market.pools, block, market.oracle_decimals)
    append_history(state.history_path, snap)
    state.last_snapshot = snap
    state.last_stats = _stats(state, market)
    write_cache(state.cache_path, {**snap, "stats": state.last_stats})
    _log_poll(snap, _spend(rpc, state), state)
    return snap


def _write_error_cache(state):
    if state.last_snapshot is None:
        log.warning("no earlier snapshot, nothing to put in the cache")
        return
    write_cache(state.cache_path, {**state.last_snapshot, "rpc_status": "error",
                                   "stats": state.last_stats})


def safe_poll(rpc, market, state, sleep=time.sleep) -> dict | None:
    """poll_once with the error policy: on failure keep the last values in the
    cache marked rpc_status "error" (never appended to history), retry once
    after RETRY_DELAY, and give up until the next poll if that fails too."""
    for attempt in (1, 2):
        try:
            return poll_once(rpc, market, state)
        except RpcError as exc:
            log.warning("poll failed (attempt %d): %s", attempt, exc)
            _spend(rpc, state)
            _write_error_cache(state)
            if attempt == 1:
                sleep(config.RETRY_DELAY)
    log.warning("giving up until the next poll")
    return None


def run_loop(rpc, market, state, sleep=time.sleep, clock=time.monotonic,
             interval=config.POLL_INTERVAL, max_polls=None) -> None:
    """Poll forever on a fixed schedule so a slow poll does not drift."""
    next_at = clock()
    polls = 0
    while max_polls is None or polls < max_polls:
        try:
            safe_poll(rpc, market, state, sleep)
        except Exception:  # not BaseException: Ctrl+C must still exit
            # Safe to log: the RPC layer wraps transport errors as RpcError,
            # whose messages are already redacted (no API key).
            log.exception("unexpected error in poll; continuing")
            try:
                _write_error_cache(state)
            except Exception:
                log.exception("could not write the error cache")
        polls += 1
        next_at += interval
        now = clock()
        if next_at < now:  # fell behind (e.g. a long outage): do not burst
            next_at = now
        sleep(max(0.0, next_at - now))


def _parse(argv):
    p = argparse.ArgumentParser(prog="python -m tracker.poller",
                                description="Poll the LINK/WETH Uniswap v3 pools.")
    p.add_argument("--once", action="store_true", help="one poll (no catch-up), write the cache, exit")
    p.add_argument("--backfill", action="store_true", help="catch up on history, write the cache, exit")
    p.add_argument("--days", type=int, default=config.HISTORY_DAYS, help="days of history (default 7)")
    return p.parse_args(argv)


def _run(args, sleep):
    rpc = Rpc.from_env()
    state = PollerState(history_path=HISTORY_PATH, cache_path=CACHE_PATH, days=args.days)
    latest = rpc.latest_block_number()
    market = load_pools(rpc, latest)
    if not Path(state.history_path).exists() and Path(SEED_PATH).exists():
        Path(state.history_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SEED_PATH, state.history_path)
        log.info("history missing: copied seed %s", SEED_PATH)
    _spend(rpc, state)

    if args.once:
        if safe_poll(rpc, market, state, sleep) is None:
            raise RpcError("poll failed twice, see the log above")
        return
    added = catch_up_to_head(rpc, market, state.history_path, args.days, sleep=sleep)
    _spend(rpc, state)
    if args.backfill:
        log.info("backfill done: %d snapshots added", added)
        write_cache_from_history(market, state)
        return
    run_loop(rpc, market, state, sleep)


def main(argv=None, sleep=time.sleep) -> int:
    args = _parse(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    try:
        _run(args, sleep)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 0
    except (RpcError, PoolCheckFailed) as exc:
        print(str(exc))
        sys.exit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
