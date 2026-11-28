"""Fixed addresses, trade sizes and tuning numbers for the tracker.

Everything here is a constant, so the other modules never hold a magic number.
Addresses are checksummed; tests/test_config.py verifies that.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# --- Chain and contracts (Ethereum mainnet) ----------------------------------
CHAIN_ID = 1
FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"  # UniswapV3Factory
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
LINK = "0x514910771AF9Ca656af840dff83E8264EcF986CA"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
CHAINLINK_LINK_ETH = "0xDC530D9457755926550b59e8ECcdaE7624181557"
CHAINLINK_DESCRIPTION = "LINK / ETH"
CHAINLINK_DECIMALS = 18

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# --- Pair: token0 = LINK, token1 = WETH (the pool sorts by address) ----------
TOKEN0 = {"address": LINK, "symbol": "LINK", "decimals": 18}
TOKEN1 = {"address": WETH, "symbol": "WETH", "decimals": 18}

# Fee tiers are in hundredths of a basis point, so 3000 = 0.3%.
CANDIDATE_FEE_TIERS = (500, 3000, 10000)
# The factory pairs each fee tier with one tick spacing.
TICK_SPACINGS = {500: 10, 3000: 60, 10000: 200}
FEE_TIERS = (500, 3000)  # the two deepest tiers in preflight (block 26512506); 10000 was far shallower

# --- Reference trade sizes (raw units, 18 decimals) --------------------------
BUY_SIZE_WETH = 3 * 10**17  # 0.3 WETH in, LINK out
SELL_SIZE_LINK = 50 * 10**18  # 50 LINK in, WETH out

# --- Polling and backfill ----------------------------------------------------
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL") or 300)  # seconds
RETRY_DELAY = 60  # seconds before the one retry after an RPC error
BACKFILL_STEP = 25  # blocks between backfilled snapshots (about 5 minutes)
HISTORY_DAYS = 7
BLOCKS_PER_DAY = 7200  # 12 s blocks
BACKFILL_PAUSE = 0.2  # seconds between backfill snapshots, to avoid 429s
DRIFT_THRESHOLD = 0.005  # pools more than 0.5% apart count as drifting

# --- Alchemy compute units per request, for the CU log -----------------------
CU_COSTS = {"eth_call": 26, "get_block": 16, "block_number": 10, "chain_id": 0}

# --- Files -------------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
HISTORY_PATH = DATA_DIR / "history.jsonl"
CACHE_PATH = DATA_DIR / "cache.json"
SEED_PATH = DATA_DIR / "history_seed.jsonl"
SCHEMA_VERSION = 1
