# Architecture

How data moves through the tracker, what each module does, and what the poller does on every poll and at startup. File formats are in [cache_schema.md](cache_schema.md).

## Data flow

```
Ethereum (Alchemy RPC)
   |  every POLL_INTERVAL: eth_blockNumber + getBlock + 1 Multicall3 eth_call
   v
tracker/poller.py ---> data/history.jsonl   (append only, one snapshot per line)
        |
        +------------> data/cache.json      (newest snapshot + stats, replaced whole)
                              ^
                              | read only
                        tracker/server.py (Member B) ---> web/ (browser)
```

- Only the poller talks to the chain and only the poller writes files.
- Only the server (Member B) reads `cache.json`. The browser never sees the API key.
- Facts from the chain go in the files. Judgments (oracle status, abnormal-price flags, the recommendation) are computed by the server per request and never written back.

## Modules

| Module | Owner | What it does |
|---|---|---|
| `tracker/config.py` | A | Addresses, fee tiers, trade sizes, polling numbers, CU costs, file paths. No logic. |
| `tracker/abi.py` | A | Function encoders and decoders for the contracts the tracker calls. |
| `tracker/rpc.py` | A | `Rpc` (real client, counts calls for the CU log), `RecordingRpc` and `FixtureRpc` (record and replay for tests), `RpcError` (message has the API key removed). |
| `tracker/pricing.py` | A | `sqrtPriceX96` to price (exact, with `Fraction`), and the tick consistency check. |
| `tracker/chain.py` | A | Startup validation (`load_pools`), the Multicall3 build and decode, `snapshot`, and `backfill`. |
| `tracker/store.py` | A | Append to `history.jsonl`, read it back (skipping bad lines), atomic write of `cache.json`. |
| `tracker/stats.py` | A | 7-day win rate, median and maximum advantage, and pool drift. Standard library only. |
| `tracker/poller.py` | A | Startup, catch-up, the polling loop and the error policy. Command line entry point. |
| `scripts/preflight.py` | A | Four live checks (tiers, archive depth, quoter, oracle). |
| `scripts/record_fixture.py` | A | Records one pinned block into `tests/fixtures/` for offline tests. |
| `tracker/thresholds.py`, `oracle.py`, `recommend.py`, `server.py`, `web/` | B | Oracle checks, recommendation rule, API and page. See the README sections written by Member B. |
| `docs/cache.schema.json`, `tests/test_contract.py` | A and B | The agreed format and the test that enforces it. Changes need both members. |

## One poll, step by step

`poll_once` in `poller.py`:

1. `rpc.latest_block_number()` pins the block (`eth_blockNumber`, 10 CU).
2. `rpc.block(n)` reads the block's number, timestamp and base fee (16 CU).
3. `chain.build_multicall` builds nine sub-calls, all with `allowFailure = true`:

   | # | Target | Call |
   |---|---|---|
   | 1 | pool 1 (500) | `slot0()` |
   | 2 | pool 1 | `liquidity()` |
   | 3 | QuoterV2 | `quoteExactInputSingle`, buy: 0.3 WETH in, LINK out, fee 500 |
   | 4 | QuoterV2 | `quoteExactInputSingle`, sell: 50 LINK in, WETH out, fee 500 |
   | 5 | pool 2 (3000) | `slot0()` |
   | 6 | pool 2 | `liquidity()` |
   | 7 | QuoterV2 | buy quote, fee 3000 |
   | 8 | QuoterV2 | sell quote, fee 3000 |
   | 9 | Chainlink LINK/ETH | `latestRoundData()` |

4. One Multicall3 `aggregate3` `eth_call` at the pinned block runs all nine (26 CU).
5. `chain.decode_multicall` decodes each result on its own. A failed sub-call turns only that value into `null`. A pool whose `sqrtPriceX96` and tick disagree gets `price_check: "failed"` and no price; a pool with no `slot0` gets `"missing"`.
6. `append_history` adds the snapshot as one line to `history.jsonl` and forces it to disk. This is also the resume mechanism.
7. `compute_stats` runs over the stored history (error lines ignored).
8. `write_cache` replaces `cache.json` with the snapshot plus `stats`.
9. The poller logs the block, prices, quotes and the CU spent (52 per poll, 10 + 16 + 26).

## Startup

`_run` in `poller.py`:

1. `Rpc.from_env()` reads `RPC_BASE_URL` and `API_KEY` from `.env`. A missing key stops the run with a plain message.
2. `load_pools(rpc, latest)` finds both pools through the factory and runs the validation checks (chain ID, non-zero pool, factory, token pair, fee, tick spacing, symbols and decimals, Chainlink description and decimals). Any failure raises `PoolCheckFailed` and the poller exits with that message. The poller never starts on data it does not understand.
3. If `data/history.jsonl` does not exist and `data/history_seed.jsonl` exists, the seed is copied to `data/history.jsonl`.
4. Unless `--once` is given, `catch_up` fills the gap:
   - `oldest = latest - days * 7200` (7 days is 50,400 blocks).
   - `start = max(last_stored_block + 25, oldest)`. With no history, `start = oldest`.
   - `end = latest - 25`. It stops before `latest` so the first live poll, which snapshots `latest`, does not duplicate it.
   - It snapshots `start`, `start + 25`, ... while the block is at most `end`. 25 blocks is about 5 minutes.
   - Each snapshot is appended as soon as it is taken, then the poller pauses 0.2 s. If it stops halfway, the next start resumes after the last stored block.
   - Stored lines with `rpc_status: "error"` are ignored when finding the last block.
5. `--backfill` writes `cache.json` from the newest stored snapshot and exits. Otherwise the poller enters the loop. The loop runs on a fixed schedule (`next_at += POLL_INTERVAL`), so a slow poll does not shift later ones. If it falls behind, it polls once and resumes the schedule instead of bursting.

## Error handling

| Situation | What happens |
|---|---|
| Any RPC failure | The client raises `RpcError`. The message has the API key replaced with `<API_KEY>`, and the original exception is dropped (`from None`) because its text can quote the URL. |
| A poll fails (`safe_poll`) | The last good snapshot is written to `cache.json` with `rpc_status: "error"` and the old stats. The poller waits 60 s (`RETRY_DELAY`) and tries once more. If that fails too, it logs "giving up until the next poll" and waits for the schedule. |
| Error snapshots | Never appended to `history.jsonl`. Stats ignore any line with `rpc_status: "error"`. |
| No earlier snapshot to show | The cache is not written, and the poller logs a warning. |
| 429 during catch-up | The same block is retried after 2, 4 and 8 s. If it still fails, or on any other error, catch-up stops with the error. A rerun resumes from the last stored block. |
| One Multicall sub-call fails | Only that value is `null`. The rest of the snapshot is kept. |
| Whole Multicall response can't be decoded | Raised as `RpcError`, so it follows the poll-failure path above. |
| Half-written last line in `history.jsonl` | Skipped on read, with a warning. |
| Windows holds `cache.json` open (a reader) | `os.replace` raises `PermissionError`. `write_cache` retries up to 5 times with a short sleep, then raises. |
| Startup validation failure | `PoolCheckFailed`: exit code 1 and a message saying which check failed. |

## One writer, one reader

- The poller is the only process that writes anything in `data/`. The server only reads.
- `history.jsonl` is append only and read by the poller itself (stats and catch-up).
- `cache.json` is written to `cache.json.tmp`, flushed to disk, then moved over `cache.json` with `os.replace`. A reader sees the old file or the new file, never half of one.
- With one writer there is nothing to lock. Do not run two pollers at once against the same `data/` folder.
- The server never writes back. Anything it computes (oracle status, flags, recommendation) exists only in its API response.
