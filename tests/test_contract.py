"""Contract tests: docs/cache.schema.json vs. snapshots, stats and sample files."""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from tracker.chain import load_pools, snapshot
from tracker.rpc import FixtureRpc
from tracker.stats import compute_stats

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "docs" / "cache.schema.json").read_text(encoding="utf-8"))
REGISTRY = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))
ADDR_A = "0x" + "aB" * 20
ADDR_B = "0x" + "12" * 20


def make_snapshot(block=23847120, ts=1764000000):
    def pool(fee, addr, buy, sell, price):
        return {"fee": fee, "pool": addr, "sqrt_price_x96": "1771595571142957166518320255467520",
                "tick": -52000, "liquidity": "48211034417892211", "price_weth_per_link": price,
                "price_check": "ok", "quote_buy_link_out": buy, "quote_sell_weth_out": sell}
    return {
        "schema_version": 1, "block": block, "timestamp": ts, "base_fee_wei": "8500000000",
        "rpc_status": "ok", "buy_size_weth": "300000000000000000",
        "sell_size_link": "50000000000000000000",
        "pools": [pool(3000, ADDR_A, "58412300000000000000", "255100000000000000", 0.00512),
                  pool(10000, ADDR_B, "58000000000000000000", "256000000000000000", 0.00513)],
        "oracle_raw": {"round_id": "18446744073709552000", "answer": "512000000000000",
                       "started_at": 1763998800, "updated_at": 1763998800,
                       "answered_in_round": "18446744073709552000", "decimals": 18},
    }


def make_cache():
    hist = [make_snapshot(23847120 - 25 * i, 1764000000 - 300 * i) for i in range(5)]
    cache = copy.deepcopy(hist[0])
    cache["stats"] = compute_stats(hist)
    return cache


def validator():
    return Draft202012Validator(SCHEMA, registry=REGISTRY)


def snapshot_validator():
    return Draft202012Validator({"$ref": SCHEMA["$id"] + "#/$defs/snapshot"}, registry=REGISTRY)


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA["$id"]


def test_valid_cache_passes():
    validator().validate(make_cache())


def test_cache_with_empty_stats_and_nulls_passes():
    c = make_cache()
    c["stats"] = compute_stats([])
    c["oracle_raw"] = None
    p = c["pools"][0]
    p.update(sqrt_price_x96=None, tick=None, liquidity=None, price_weth_per_link=None,
             price_check="missing", quote_buy_link_out=None, quote_sell_weth_out=None)
    validator().validate(c)


def test_negative_oracle_answer_allowed():
    c = make_cache()
    c["oracle_raw"]["answer"] = "-5"
    validator().validate(c)


def _mutations():
    def liq_number(c): c["pools"][0]["liquidity"] = 48211034417892211
    def version(c): c["schema_version"] = 2
    def three_pools(c): c["pools"].append(copy.deepcopy(c["pools"][0]))
    def one_pool(c): c["pools"].pop()
    def extra_top(c): c["cache_status"] = "stale_read"
    def no_stats(c): del c["stats"]
    def bad_status(c): c["rpc_status"] = "weird"
    def bad_check(c): c["pools"][0]["price_check"] = "maybe"
    def bad_addr(c): c["pools"][0]["pool"] = "0x123"
    def quote_float(c): c["pools"][1]["quote_buy_link_out"] = 5.8e19
    def neg_liq(c): c["pools"][0]["liquidity"] = "-1"
    def win_key(c): c["stats"]["buy"]["wins"]["fast"] = 1
    def stats_extra(c): c["stats"]["buy"]["bonus"] = 1
    def block_str(c): c["block"] = "23847120"
    return [liq_number, version, three_pools, one_pool, extra_top, no_stats, bad_status,
            bad_check, bad_addr, quote_float, neg_liq, win_key, stats_extra, block_str]


@pytest.mark.parametrize("mutate", _mutations(), ids=lambda f: f.__name__)
def test_broken_variants_fail(mutate):
    c = make_cache()
    mutate(c)
    assert not validator().is_valid(c)


def test_history_line_validates_against_snapshot_def():
    v = snapshot_validator()
    v.validate(make_snapshot())
    assert not v.is_valid(make_cache())  # stats is not allowed in a history line
    bad = make_snapshot()
    bad["pools"][0]["liquidity"] = 1
    assert not v.is_valid(bad)


def _cache_shaped(obj):
    """Tolerantly find cache-shaped objects: the object itself, items of a list,
    or values nested under a wrapper dict (e.g. an API response). Response-time
    keys (cache_status, oracle, recommendation, ...) are stripped by the caller
    because they belong to the API response, not to the file."""
    if isinstance(obj, list):
        for item in obj:
            yield from _cache_shaped(item)
    elif isinstance(obj, dict):
        if "schema_version" in obj and "pools" in obj:
            yield obj
        else:
            for val in obj.values():
                if isinstance(val, (dict, list)):
                    yield from _cache_shaped(val)


def test_web_sample_state_validates():
    path = ROOT / "web" / "sample_state.json"
    if not path.exists():
        pytest.skip("web/sample_state.json does not exist yet (Member B's file)")
    found = list(_cache_shaped(json.loads(path.read_text(encoding="utf-8"))))
    if not found:
        pytest.skip("no cache-shaped object found in web/sample_state.json")
    allowed = set(SCHEMA["properties"])
    v = validator()
    for obj in found:
        v.validate({k: val for k, val in obj.items() if k in allowed})


def test_fixture_snapshot_validates():
    fixtures = ROOT / "tests" / "fixtures"
    files = sorted(p for p in fixtures.glob("block_*.json") if not p.name.endswith(".expected.json"))
    if not files:
        pytest.skip("no recorded fixture in tests/fixtures/")
    rpc = FixtureRpc(files[-1])
    block = rpc.latest_block_number()
    market = load_pools(rpc, block)
    snap = snapshot(rpc, market.pools, block, market.oracle_decimals)
    snapshot_validator().validate(snap)  # a history line
    cache = {**snap, "stats": compute_stats([snap])}
    validator().validate(cache)  # cache.json
    assert cache["stats"]["snapshots"] == 1
