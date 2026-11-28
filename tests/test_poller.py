import json

import pytest

from tests.fakes import build_fake_chain
from tracker import config, poller
from tracker.chain import load_pools
from tracker.rpc import RpcError
from tracker.store import append_history, read_history

LATEST = 23_000_000 + 1000
STEP = 25


def _setup(tmp_path, blocks=(LATEST,)):
    rpc = build_fake_chain(list(blocks))
    market = load_pools(rpc, LATEST)
    state = poller.PollerState(history_path=tmp_path / "history.jsonl", cache_path=tmp_path / "cache.json")
    return rpc, market, state


def _snap_blocks(rpc):
    """Blocks of the multicall requests (every snapshot makes one)."""
    return [b for (to, _d, b) in rpc.eth_call_log if to.lower() == config.MULTICALL3.lower()]


def test_poll_once_writes_cache_and_history(tmp_path):
    rpc, market, state = _setup(tmp_path)
    snap = poller.poll_once(rpc, market, state)
    cache = json.loads(state.cache_path.read_text())
    assert cache["block"] == LATEST == snap["block"]
    assert cache["stats"]["snapshots"] == 1
    assert len(read_history(state.history_path)) == 1
    assert state.last_stats == cache["stats"]


def test_error_keeps_last_values_and_retries_once(tmp_path):
    rpc, market, state = _setup(tmp_path)
    poller.poll_once(rpc, market, state)
    rpc.fail_next(1)
    sleeps = []
    result = poller.safe_poll(rpc, market, state, sleep=sleeps.append)
    assert result is not None  # retry succeeded
    assert sleeps == [config.RETRY_DELAY]

    rpc.fail_next(2)
    sleeps.clear()
    assert poller.safe_poll(rpc, market, state, sleep=sleeps.append) is None
    assert sleeps == [config.RETRY_DELAY]
    cache = json.loads(state.cache_path.read_text())
    assert cache["rpc_status"] == "error"
    assert cache["block"] == LATEST
    assert cache["pools"] == state.last_snapshot["pools"]
    assert "stats" in cache
    assert len(read_history(state.history_path)) == 2  # the two good polls only


def test_error_with_no_previous_snapshot(tmp_path):
    rpc, market, state = _setup(tmp_path)
    rpc.fail_next(2)
    assert poller.safe_poll(rpc, market, state, sleep=lambda s: None) is None
    assert not state.cache_path.exists()


def test_catch_up_empty_history(tmp_path):
    days = 1
    oldest = LATEST - days * config.BLOCKS_PER_DAY
    expected = list(range(oldest, LATEST - STEP + 1, STEP))
    rpc = build_fake_chain(expected)
    market = load_pools(rpc, expected[0])
    rpc.eth_call_log.clear()
    sleeps = []
    path = tmp_path / "h.jsonl"
    n = poller.catch_up(rpc, market, path, LATEST, days=days, step=STEP, sleep=sleeps.append)
    assert n == len(expected)
    assert _snap_blocks(rpc) == expected
    assert [s["block"] for s in read_history(path)] == expected


def test_catch_up_resumes_and_is_idempotent(tmp_path):
    days = 1
    oldest = LATEST - days * config.BLOCKS_PER_DAY
    all_blocks = list(range(oldest, LATEST - STEP + 1, STEP))
    rpc = build_fake_chain(all_blocks)
    market = load_pools(rpc, all_blocks[0])
    path = tmp_path / "h.jsonl"
    done = all_blocks[:10]
    for b in done:
        append_history(path, {"block": b, "rpc_status": "ok"})
    # an error line past the end must be ignored when finding the last block
    append_history(path, {"block": LATEST, "rpc_status": "error"})
    rpc.eth_call_log.clear()
    n = poller.catch_up(rpc, market, path, LATEST, days=days, step=STEP, sleep=lambda s: None)
    assert _snap_blocks(rpc) == all_blocks[10:]
    assert n == len(all_blocks) - 10
    rpc.eth_call_log.clear()
    assert poller.catch_up(rpc, market, path, LATEST, days=days, step=STEP, sleep=lambda s: None) == 0
    assert rpc.eth_call_log == []


def test_main_missing_key(monkeypatch, capsys):
    def boom():
        raise RpcError("Missing API key: open .env and paste your key after API_KEY=")

    monkeypatch.setattr(poller.Rpc, "from_env", staticmethod(boom))
    with pytest.raises(SystemExit) as exc:
        poller.main(["--once"])
    assert exc.value.code == 1
    assert "Missing API key: open .env and paste your key after API_KEY=" in capsys.readouterr().out


def test_main_once_copies_seed(monkeypatch, tmp_path):
    seed = tmp_path / "seed.jsonl"
    append_history(seed, {"block": 1, "timestamp": 1_763_000_000, "rpc_status": "ok", "pools": []})
    rpc = build_fake_chain([LATEST])
    monkeypatch.setattr(poller.Rpc, "from_env", staticmethod(lambda: rpc))
    monkeypatch.setattr(poller, "HISTORY_PATH", tmp_path / "data" / "history.jsonl")
    monkeypatch.setattr(poller, "CACHE_PATH", tmp_path / "data" / "cache.json")
    monkeypatch.setattr(poller, "SEED_PATH", seed)
    assert poller.main(["--once"], sleep=lambda s: None) == 0
    history = read_history(tmp_path / "data" / "history.jsonl")
    assert [s["block"] for s in history] == [1, LATEST]
    assert (tmp_path / "data" / "cache.json").exists()


def test_run_loop_schedule(tmp_path):
    rpc, market, state = _setup(tmp_path)
    sleeps, now = [], [0.0]

    def clock():
        return now[0]

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    poller.run_loop(rpc, market, state, sleep=sleep, clock=clock, interval=300, max_polls=2)
    assert sleeps == [300, 300]


def _fast_loop(rpc, market, state, max_polls):
    poller.run_loop(rpc, market, state, sleep=lambda s: None, clock=lambda: 0.0,
                    interval=300, max_polls=max_polls)


def test_run_loop_survives_unexpected_error(tmp_path, monkeypatch):
    rpc, market, state = _setup(tmp_path)
    poller.poll_once(rpc, market, state)  # so there is a last snapshot
    real = poller.poll_once
    calls = []

    def flaky(*args):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("bad multicall")
        return real(*args)

    monkeypatch.setattr(poller, "poll_once", flaky)
    _fast_loop(rpc, market, state, 2)  # must not raise
    assert len(calls) == 2
    assert len(read_history(state.history_path)) == 2  # setup poll + second loop poll
    # the second poll succeeded and rewrote the cache without an error flag
    assert json.loads(state.cache_path.read_text()).get("rpc_status") != "error"


def test_run_loop_writes_error_cache_after_unexpected_error(tmp_path, monkeypatch):
    rpc, market, state = _setup(tmp_path)
    poller.poll_once(rpc, market, state)

    def boom(*args):
        raise ValueError("bad multicall")

    monkeypatch.setattr(poller, "poll_once", boom)
    _fast_loop(rpc, market, state, 1)
    cache = json.loads(state.cache_path.read_text())
    assert cache["rpc_status"] == "error"
    assert cache["block"] == LATEST


def test_run_loop_keyboard_interrupt_propagates(tmp_path, monkeypatch):
    rpc, market, state = _setup(tmp_path)

    def stop(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(poller, "poll_once", stop)
    with pytest.raises(KeyboardInterrupt):
        _fast_loop(rpc, market, state, 2)


def test_catch_up_to_head_repeats_until_nothing_added(tmp_path):
    days = 1
    first_latest = LATEST
    second_latest = LATEST + 3 * STEP
    oldest = first_latest - days * config.BLOCKS_PER_DAY
    blocks = list(range(oldest, second_latest - STEP + 1, STEP))
    rpc = build_fake_chain(blocks)
    market = load_pools(rpc, blocks[0])
    # latest_block_number: first pass sees LATEST, later passes see second_latest
    heads = iter([first_latest, second_latest, second_latest])
    rpc.latest_block_number = lambda: next(heads)
    path = tmp_path / "h.jsonl"
    total = poller.catch_up_to_head(rpc, market, path, days, sleep=lambda s: None)
    first = len(range(oldest, first_latest - STEP + 1, STEP))
    assert total == len(blocks) > first
    assert [s["block"] for s in read_history(path)] == blocks
    assert next(heads, None) is None  # stopped right after the pass that added 0
