# Uniswap Live Tracker (LINK/WETH)

BoilerBlockchain Final Project

A Python tracker for the LINK/WETH pair on Uniswap v3 (Ethereum mainnet). Every 5 minutes it reads two fee-tier pools, asks the Uniswap quoter what a fixed buy and a fixed sell would return in each pool, and records the Chainlink LINK/ETH answer next to them. It keeps 7 days of history and computes how often each pool gave the better quote.

It is read-only. It only makes `view` calls, holds no wallet or signing key, and cannot trade.

## What it claims and doesn't claim

- **It claims:** "For this trade size, pool A gives you more tokens than pool B right now. Over the last 7 days, A was better X% of the time."
- **It doesn't claim:** "Buy now" or "sell now." Whether to trade at all depends on future prices, which on-chain data can't predict.
- A quote is not a guaranteed price. It can change before a trade executes.

## Setup (Windows)

You need 64-bit Windows and a free API key from an Ethereum RPC provider such as Alchemy. No funds or wallet are needed. You don't need to install Python first.

From the project folder, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

The script:

1. Looks for a 64-bit Python 3.14. If there isn't one, it downloads the official installer from python.org, checks it against the SHA-256 checksum python.org publishes, and installs it for your user only. Other Python versions stay installed, and it doesn't change PATH or which Python opens `.py` files.
2. Builds `.venv` with Python 3.14 and installs `requirements.txt` into it. If an existing `.venv` was built with a different Python, it deletes and rebuilds it.
3. Creates `.env` from `.env.example` if you don't have one yet.

You can rerun it at any time. It keeps a `.venv` that is already correct.

The project needs Python 3.14 specifically because `web3` depends on `ckzg`, a C library whose prebuilt Windows packages cover 64-bit Python 3.9 to 3.14 only. On a newer or 32-bit Python, pip tries to compile it and fails unless a C compiler is installed.

Then open `.env` and paste your key after `API_KEY=`. To use Infura, set `RPC_BASE_URL=https://mainnet.infura.io/v3/`. `.env` is ignored by git. Never commit a real key.

## Setup (macOS / Linux)

You need Python 3.14 (or another version that has a prebuilt `ckzg` package for your platform).

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Then paste your key after `API_KEY=` in `.env`. In the commands below, use `.venv/bin/python` where the Windows examples say `.venv\Scripts\python`.

## Run

All commands run from the project folder.

### 1. Pre-flight (optional, about 676 CU)

```powershell
.venv\Scripts\python scripts\preflight.py
```

Runs four checks against the real chain and prints which values to put in `tracker/config.py`: fee tiers by liquidity, archive depth (7 days back), the quoter at the latest and a past block, and the Chainlink feed. It exits non-zero if a check fails. The results of our run are below.

### 2. Poller

```powershell
.venv\Scripts\python -m tracker.poller
```

| Flag | What it does |
|---|---|
| (none) | Catch up on missing history, then poll forever. |
| `--once` | Take one snapshot at the latest block, write `data/cache.json`, exit. No catch-up. |
| `--backfill` | Catch up on history only, write `data/cache.json` from the newest stored snapshot, exit. |
| `--days N` | Days of history to keep and use for statistics (default 7). |

The poll interval comes from the `POLL_INTERVAL` environment variable (seconds, default 300). For the demo, use 60:

```powershell
$env:POLL_INTERVAL = "60"; .venv\Scripts\python -m tracker.poller
```

On macOS/Linux: `POLL_INTERVAL=60 .venv/bin/python -m tracker.poller`. You can also put it in `.env`. Press Ctrl+C to stop.

The poller writes two files in `data/` (ignored by git):

| File | Content |
|---|---|
| `data/history.jsonl` | One snapshot per line, appended after every poll. |
| `data/cache.json` | The newest snapshot plus the 7-day `stats`. Replaced after every poll. |

The formats are in [docs/cache_schema.md](docs/cache_schema.md). The data flow and the poller's internals are in [docs/architecture.md](docs/architecture.md).

### History seed

A 7-day backfill is 2,016 snapshots. One member runs it once and shares the result, so the other doesn't spend the compute units again. The poller looks for the seed at `data/history_seed.jsonl`. `data/` is ignored by git, so the seed is shared outside the repo:

1. The member who ran the backfill shares `data/history.jsonl` (for example as a GitHub release asset or on a shared drive), renamed to `history_seed.jsonl`.
2. The other member puts it in `data/`.
3. On first start, if `data/history.jsonl` does not exist, the poller copies the seed to it. Then it fills only the gap between the seed and now.

If `data/history.jsonl` already exists, the seed is ignored.

### Tests

```powershell
.venv\Scripts\python -m pytest -q
```

All tests run offline and need no API key. The tests that use real chain data replay the recording in `tests/fixtures/` (one pinned block, 26 recorded RPC calls). One test, `tests/test_live.py`, calls the real chain. It runs only when `API_KEY` is set in the environment, and is skipped otherwise.

To record a new fixture (needs a key in `.env`, makes about 26 RPC calls):

```powershell
.venv\Scripts\python scripts\record_fixture.py            # latest block
.venv\Scripts\python scripts\record_fixture.py --block N  # a specific block
```

It writes `tests/fixtures/block_<N>.json` and `block_<N>.expected.json`, and fails if the files contain the key or a URL.

## What it reads

| Input | Value |
|---|---|
| Network | Ethereum mainnet (chain ID 1) |
| UniswapV3Factory | `0x1F98431c8aD98523631AE4a59f267346ea31F984` |
| QuoterV2 | `0x61fFE014bA17989E743c5F6cB21bF9697530B21e` |
| Multicall3 | `0xcA11bde05977b3631167028862bE2a173976CA11` |
| LINK (token0) | `0x514910771AF9Ca656af840dff83E8264EcF986CA` |
| WETH (token1) | `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` |
| Chainlink LINK/ETH | `0xDC530D9457755926550b59e8ECcdaE7624181557` (18 decimals) |
| Fee tiers | 500 (0.05%) and 3000 (0.3%) |
| 500 pool | `0x5d4F3C6fA16908609BAC31Ff148Bd002AA6b8c83` |
| 3000 pool | `0xa6Cc3C2531FdaA6Ae1A3CA84c2855806728693e8` |
| Buy size | 0.3 WETH in, LINK out |
| Sell size | 50 LINK in, WETH out |
| Block | Latest block at poll time, pinned for every read of that snapshot |

The pool addresses are not hardcoded. The poller gets them from `factory.getPool(LINK, WETH, fee)` at startup. The addresses above are what the factory returned in our recording.

## Pre-flight results

Run at block 26,512,506 (Nov 28, 2026).

| Fee tier | Pool liquidity | Buy 0.3 WETH gives | Sell 50 LINK gives |
|---|---|---|---|
| 500 | 1.53e22 | 54.117 LINK | 0.27676 WETH |
| 3000 | 3.39e23 | 54.072 LINK | 0.27574 WETH |
| 10000 | 2.28e18 | 28.46 LINK | 0.04469 WETH |

- **Tiers chosen:** 500 and 3000. The 10000 pool is far too shallow: 0.3 WETH bought only 28.46 LINK there, against about 54.1 in the other two.
- **The two tiers compete.** The 500 pool is better at the latest block. Seven days earlier the 3000 pool narrowly won the sell (0.255465 vs 0.255406 WETH).
- **Archive reads:** 7 days back works on Alchemy.
- **Chainlink LINK/ETH:** description `LINK / ETH`, 18 decimals, answer 0.00557078 WETH per LINK, last updated 317.6 minutes before the latest block. The heartbeat is long, so a stale limit has to be generous. Pool prices were within 0.6 to 0.7% of the answer.

## Validation checks

At startup, for each pool, the tracker stops with a specific error if any of these fail:

| Check | What it catches |
|---|---|
| Chain ID is 1 | An RPC URL for the wrong network |
| `getPool` is not the zero address | A wrong token or fee tier, or a pool that doesn't exist |
| `pool.factory()` is the Uniswap factory | A pool that didn't come from the expected factory |
| `token0` and `token1` are LINK and WETH | Wrong token inputs |
| Fee matches the tier, and tick spacing matches (10 for 500, 60 for 3000) | Mismatched fee settings |
| Symbols are LINK and WETH, both with 18 decimals | Wrong assumptions about the tokens, which would skew the price |
| Chainlink description is `LINK / ETH` and decimals are 18 | The wrong feed |

One more check runs on every snapshot, not only at startup:

| Check | What it catches |
|---|---|
| Price from `sqrtPriceX96` agrees with `1.0001^tick` | Mistakes in the price formula or inconsistent data |

If this one fails, the tracker does not stop. It marks that pool `"price_check": "failed"` in the snapshot, keeps the raw values, and leaves the price empty. If `slot0` itself fails, the pool is marked `"missing"`. A failed sub-call inside the Multicall blanks only that value; the rest of the snapshot still decodes.

If an RPC request fails, the error message has the API key replaced with `<API_KEY>`, because these errors can quote the full URL.

## Compute unit (CU) budget

Alchemy's free plan gives 30M CU per month. The costs below are the values in `tracker/config.py` (`CU_COSTS`), and the poller logs the CU each poll spends.

| Request | CU |
|---|---|
| `eth_call` | 26 |
| `eth_getBlockByNumber` | 16 |
| `eth_blockNumber` | 10 |

One poll is `eth_blockNumber` + `eth_getBlockByNumber` + one Multicall3 `eth_call`. That is 10 + 16 + 26 = 52 CU, no matter how many pools or quotes it contains.

| Item | Count | CU |
|---|---|---|
| Polling every 5 minutes (8,640 polls per month) | 8,640 x 52 | about 450K per month |
| 7-day backfill, run once | 2,016 x 42 (block + Multicall) | about 85K |
| Catch-up after a restart | only the missing snapshots | small |
| Pre-flight | one run | about 676 |
| Fixture recording | 26 calls | small |

For comparison, the Milestone 6 script cost about 390 CU per run, for one pool. Polled every minute, that would be about 17M CU per month.

## Troubleshooting

**`Failed to build ckzg` during install:** pip ran on a Python that `ckzg` has no prebuilt package for, usually a newer default Python. On Windows, run `setup.ps1` instead of installing by hand. It rebuilds `.venv` with Python 3.14.

**`ModuleNotFoundError: No module named 'dotenv'`** (or `'web3'`): `.venv` exists but is missing packages, usually because an earlier install failed. Run `setup.ps1` again. Don't fix it by running `pip install` on your system Python; that installs into a different place from `.venv`.

**`Missing API key: open .env and paste your key after API_KEY=`:** `.env` doesn't exist or `API_KEY` is empty.

**The poller exits with a message like `expected chain 1 ...` or `unexpected pair ...`:** a startup validation check failed (see the table above). The message says which one.

**`poll failed (attempt 1)` in the log (RPC error, keeping last values):** a poll could not read the chain. The poller does not stop. It writes the last good snapshot to `cache.json` with `"rpc_status": "error"`, waits 60 seconds, and tries once more. If that also fails, it waits for the next scheduled poll. An error snapshot is never appended to `history.jsonl`, and statistics ignore it.

**HTTP 429 (too many requests):** during catch-up, the poller waits 2, 4 and 8 seconds and retries the same block. If it is still rate limited, it stops with the error. Run the poller again and it resumes from the last saved block. During normal polling, a 429 is treated like any other RPC error (see above).

**A broken last line in `history.jsonl`:** a crash can leave a half-written line. It is skipped on read.

**Archive errors during catch-up:** the provider can't serve old blocks. Use a smaller `--days`, or a plan with archive access.

## Limitations

- **Trust in the RPC provider:** the tracker believes what the provider returns. It doesn't verify state with proofs.
- **Quotes are not executed trades:** the price can move, or be sandwiched, before a trade lands.
- **Snapshots, not a stream:** 5-minute snapshots miss gaps that arbitrage closes within a few blocks. The statistics describe typical differences, not every opportunity.
- **Two tiers only:** one pair, two fee tiers, Uniswap v3 only. The 10000 tier is too thin to compare fairly.
- **Concentrated liquidity:** the `liquidity` value is only roughly comparable between tiers. The comparison uses the quotes instead.
- **Long Chainlink heartbeat:** the LINK/ETH feed can be hours old and still valid, so it is only a rough sanity check on pool prices.

## Security and trust assumptions

_Written by Member B._

## Bounty claims

_Written by Member B._

## Contribution statement

_Written by Member B._

## Web page / server

_Written by Member B._

## Sources

- [Uniswap v3 Ethereum deployments](https://developers.uniswap.org/docs/protocols/v3/deployments/v3-ethereum-deployments): factory and WETH addresses
- [`IUniswapV3PoolState.sol`](https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/pool/IUniswapV3PoolState.sol): `slot0` and `liquidity`
- [`IUniswapV3PoolImmutables.sol`](https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/pool/IUniswapV3PoolImmutables.sol): `factory`, `token0`, `token1`, `fee`, `tickSpacing`
- [`IUniswapV3Factory.sol`](https://github.com/Uniswap/v3-core/blob/main/contracts/interfaces/IUniswapV3Factory.sol): `getPool`
- [Uniswap v3-periphery](https://github.com/Uniswap/v3-periphery): `QuoterV2`
- [Multicall3](https://github.com/mds1/multicall): `aggregate3`
