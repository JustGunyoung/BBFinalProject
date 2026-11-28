"""RPC wrappers: error handling, key redaction, recording and replay."""

import pytest

from tracker import abi, config
from tracker.chain import load_pools
from tracker.rpc import FixtureRpc, RecordingRpc, Rpc, RpcError
from tests.fakes import build_fake_chain


def test_rate_limit_is_detected():
    assert RpcError("HTTP 429 Client Error").rate_limited
    assert RpcError("Too Many Requests").rate_limited
    assert not RpcError("connection refused").rate_limited


def test_errors_are_wrapped_and_key_is_redacted():
    rpc = Rpc("http://127.0.0.1:9", "SECRETKEY123")  # nothing listens on port 9
    with pytest.raises(RpcError) as info:
        rpc.latest_block_number()
    assert "SECRETKEY123" not in str(info.value)
    assert rpc.calls["block_number"] == 1


def test_record_then_replay(tmp_path):
    fake = build_fake_chain([23_000_100])
    recorder = RecordingRpc(fake)
    market = load_pools(recorder, 23_000_100)
    recorder.block(23_000_100)
    path = tmp_path / "fixture.json"
    recorder.dump(path)

    replay = FixtureRpc(path)
    assert load_pools(replay, 23_000_100).pools == market.pools
    assert replay.block(23_000_100)["number"] == 23_000_100
    with pytest.raises(RpcError):
        replay.block(5)
    with pytest.raises(RpcError):
        replay.eth_call(config.FACTORY, abi.POOL_FEE.encode(), 1)


def test_fixture_error_entry_raises():
    key = f"{config.FACTORY.lower()}|0x{abi.POOL_FEE.encode().hex()}|7"
    rpc = FixtureRpc({"chain_id": 1, "calls": {key: {"error": "boom"}}})
    with pytest.raises(RpcError, match="boom"):
        rpc.eth_call(config.FACTORY, abi.POOL_FEE.encode(), 7)
