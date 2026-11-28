"""Record a real-chain fixture: python scripts/record_fixture.py [--block N]

Writes tests/fixtures/block_<n>.json (FixtureRpc format) and
block_<n>.expected.json (the decoded snapshot) for offline tests.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tracker import chain  # noqa: E402
from tracker.rpc import RecordingRpc, Rpc, RpcError  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Record a fixture at one pinned block.")
    parser.add_argument("--block", type=int, default=None, help="block to pin (default: latest)")
    args = parser.parse_args(argv)
    try:
        inner = Rpc.from_env()
        rpc = RecordingRpc(inner)
        block = args.block if args.block is not None else rpc.latest_block_number()
        rpc.record["latest"] = block  # replays must see the pinned block as latest
        market = chain.load_pools(rpc, block)
        snap = chain.snapshot(rpc, market.pools, block, market.oracle_decimals)
    except (RpcError, chain.PoolCheckFailed) as exc:
        print(exc)
        return 1

    path = FIXTURE_DIR / f"block_{block}.json"
    rpc.dump(path)
    expected = FIXTURE_DIR / f"block_{block}.expected.json"
    expected.write_text(json.dumps(snap, indent=2), encoding="utf-8")

    text = path.read_text(encoding="utf-8") + expected.read_text(encoding="utf-8")
    assert inner._api_key not in text and "http" not in text.lower(), "dump contains a key or URL"
    print(f"Wrote {path}")
    print(f"Wrote {expected}")
    print(f"RPC calls: {dict(inner.calls)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
