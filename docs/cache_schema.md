# Cache and history format

Machine-readable schema: [`cache.schema.json`](cache.schema.json) (JSON Schema draft 2020-12,
`$id` `https://uniswap-tracker.example/schemas/cache.schema.json`). This page is the human-readable
version. Both members code against it. **Changing the schema needs the other member's review and a
`schema_version` bump.** `tests/test_contract.py` fails if the poller's output or
`web/sample_state.json` drifts from the schema.

Pair: token0 = LINK, token1 = WETH, both 18 decimals. Price is WETH per LINK.

## The two files

| File | Content | Written by | Read by |
| --- | --- | --- | --- |
| `data/history.jsonl` | One **snapshot** per line (append only) | Poller (A) | Poller (stats, catch-up) |
| `data/cache.json` | The newest snapshot **plus** a `stats` object | Poller (A), via `cache.json.tmp` + `os.replace()` | Server (B) |

A history line is validated against `#/$defs/snapshot`; `cache.json` against the schema root.
Both are strict: unknown fields are rejected (`additionalProperties: false`).
Fields the server adds to an API response (for example `cache_status`, oracle status,
recommendation) are not part of the file and never appear in it.

## Snapshot fields (all owned by A)

All big integers are decimal strings (they exceed JavaScript's exact range).

| Field | Type | Unit | Meaning |
| --- | --- | --- | --- |
| `schema_version` | const `1` | - | Format version. Readers refuse other values. |
| `block` | integer | block number | Block the snapshot was pinned to. |
| `timestamp` | integer | Unix seconds (UTC) | Timestamp of that block. |
| `base_fee_wei` | string `^[0-9]+$` | wei | Block base fee. |
| `rpc_status` | `"ok"` or `"error"` | - | `"error"`: the poll failed and values repeat the previous snapshot; statistics ignore it. |
| `buy_size_weth` | string `^[0-9]+$` | raw WETH (wei) | Input size for the buy direction. |
| `sell_size_link` | string `^[0-9]+$` | raw LINK (1e-18 LINK) | Input size for the sell direction. |
| `pools` | array, exactly 2 | - | Pools in the order of `config.FEE_TIERS`. |
| `oracle_raw` | object or null | - | Raw Chainlink `latestRoundData`; null if the call failed. |

### `pools[]`

| Field | Type | Unit | Meaning |
| --- | --- | --- | --- |
| `fee` | integer | hundredths of a bip (3000 = 0.3%) | Fee tier. |
| `pool` | string, `^0x[0-9a-fA-F]{40}$` | address | Checksummed pool address. |
| `sqrt_price_x96` | string or null | Q64.96 fixed point | `slot0.sqrtPriceX96`; null if the sub-call failed. |
| `tick` | integer or null | tick | `slot0.tick`; null if the sub-call failed. |
| `liquidity` | string or null | raw liquidity | `liquidity()`; null if failed. |
| `price_weth_per_link` | number or null | WETH per LINK | Display price derived from `sqrt_price_x96`. |
| `price_check` | `"ok"`, `"failed"`, `"missing"` | - | `failed`: sqrtPrice and tick disagree; `missing`: no slot0 data. |
| `quote_buy_link_out` | string or null | raw LINK | QuoterV2 output for `buy_size_weth`; null if the quote failed. |
| `quote_sell_weth_out` | string or null | raw WETH | QuoterV2 output for `sell_size_link`; null if the quote failed. |

### `oracle_raw`

| Field | Type | Unit | Meaning |
| --- | --- | --- | --- |
| `round_id` | string `^[0-9]+$` | - | Round id (uint80). |
| `answer` | string `^-?[0-9]+$` | feed decimals | Signed (int256), so a negative value is representable. |
| `started_at` | integer | Unix seconds | Round start. |
| `updated_at` | integer | Unix seconds | Last update. |
| `answered_in_round` | string `^[0-9]+$` | - | Round the answer was computed in. |
| `decimals` | integer 0-255 | - | Feed decimals. |

## `stats` (only in `cache.json`, owned by A)

| Field | Type | Meaning |
| --- | --- | --- |
| `window` | string or null | `YYYY-MM-DD/YYYY-MM-DD`, UTC dates of the first and last snapshot in the window; null if no snapshots. |
| `start_ts`, `end_ts` | integer or null | Timestamps of the first and last snapshot in the window. |
| `snapshots` | integer | Snapshots in the window. |
| `drift.threshold` | number | Relative price gap counted as drift (default 0.005 = 0.5%). |
| `drift.compared` | integer | Snapshots where both pool prices were usable. |
| `drift.rate` | number or null | Share of compared snapshots with `abs(p1 - p2) / min(p1, p2) > threshold`; null when none compared. |
| `buy`, `sell` | object | Per-direction results (below). |

Per direction: `compared` (snapshots where both quotes exist), `wins` (object keyed by fee string
plus `"tie"`), `win_rate` (object keyed by fee string, `wins[fee] / compared`, 0 when nothing
was compared), `median_advantage` and `max_advantage` (number or null).

Definitions:

- **Window:** snapshots with `timestamp >= newest_timestamp - days * 86400` (default 7 days).
  Snapshots with `rpc_status == "error"` are excluded entirely; input is sorted by block and
  duplicate blocks keep the last one.
- **Winner:** the pool with the larger output (compared as integers, never floats). Equal outputs are a tie
  and record no advantage.
- **Advantage:** `(winner_out - loser_out) / loser_out`, computed exactly, then converted to a float.
  If the loser's output is 0 the win counts but no advantage is recorded.
- **Win rates plus tie share** (`wins.tie / compared`) sum to 1 when `compared > 0`.
- A snapshot with a failed quote is excluded from that direction only; one with a missing or
  failed price is excluded from drift only.

## The six Cache Contract rules (summary)

1. **One writer, one reader.** Only the poller writes data files; `cache.json` is replaced atomically
   (`cache.json.tmp` then `os.replace()`); a half-written last history line is skipped on read.
2. **Raw data in the file, interpretation in the server.** Oracle status, abnormal-price flags and
   recommendations are computed per request by B and never written to disk.
3. **Big integers as strings**, next to a readable float for display.
4. **Versioned schema with a contract test.** `schema_version` is 1; changes need review and a bump;
   the server refuses unknown versions with a banner.
5. **The server keeps the last good copy.** Re-read only when mtime changes; on a failed read serve the
   last good copy with `cache_status: "stale_read"`; with none, return `{"status": "no_data"}`.
6. **Split files.** Each member edits only their own modules; `data/` is git-ignored and the 7-day
   backfill is shared once as `history_seed.jsonl`.

## Example `cache.json`

Numbers are illustrative only.

```json
{
  "schema_version": 1,
  "block": 23847120,
  "timestamp": 1764000000,
  "base_fee_wei": "8500000000",
  "rpc_status": "ok",
  "buy_size_weth": "300000000000000000",
  "sell_size_link": "50000000000000000000",
  "pools": [
    {
      "fee": 3000,
      "pool": "0xaBaBaBaBaBaBaBaBaBaBaBaBaBaBaBaBaBaBaBaB",
      "sqrt_price_x96": "1771595571142957166518320255467520",
      "tick": -52000,
      "liquidity": "48211034417892211",
      "price_weth_per_link": 0.00512,
      "price_check": "ok",
      "quote_buy_link_out": "58412300000000000000",
      "quote_sell_weth_out": "255100000000000000"
    },
    {
      "fee": 10000,
      "pool": "0x1212121212121212121212121212121212121212",
      "sqrt_price_x96": "1771800000000000000000000000000000",
      "tick": -51990,
      "liquidity": "9200000000000000",
      "price_weth_per_link": 0.00513,
      "price_check": "ok",
      "quote_buy_link_out": "58000000000000000000",
      "quote_sell_weth_out": "256000000000000000"
    }
  ],
  "oracle_raw": {
    "round_id": "18446744073709552000",
    "answer": "512000000000000",
    "started_at": 1763998800,
    "updated_at": 1763998800,
    "answered_in_round": "18446744073709552000",
    "decimals": 18
  },
  "stats": {
    "window": "2025-11-17/2025-11-24",
    "start_ts": 1763395200,
    "end_ts": 1764000000,
    "snapshots": 2016,
    "drift": {"threshold": 0.005, "compared": 2016, "rate": 0.12},
    "buy": {
      "compared": 2010,
      "wins": {"3000": 1650, "10000": 350, "tie": 10},
      "win_rate": {"3000": 0.8209, "10000": 0.1741},
      "median_advantage": 0.0015,
      "max_advantage": 0.02
    },
    "sell": {
      "compared": 2012,
      "wins": {"3000": 900, "10000": 1100, "tie": 12},
      "win_rate": {"3000": 0.4473, "10000": 0.5467},
      "median_advantage": 0.0011,
      "max_advantage": 0.015
    }
  }
}
```
