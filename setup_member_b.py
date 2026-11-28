import os

# Define the complete file layout and exact contents for Member B
project_files = {
    "tracker/thresholds.py": '''# tracker/thresholds.py

ORACLE_HEARTBEAT_LIMIT = 3600  # 1 hour in seconds
ABNORMAL_PRICE_THRESHOLD = 0.05  # 5% deviation from Chainlink
ABOUT_THE_SAME_THRESHOLD = 0.0001  # 0.01% advantage threshold
''',

    "tracker/oracle.py": '''# tracker/oracle.py
import time
from tracker.thresholds import ORACLE_HEARTBEAT_LIMIT

def check_oracle(oracle_raw, current_time=None):
    """
    Checks oracle_raw from cache.json and returns its status and price.
    """
    if current_time is None:
        current_time = time.time()
        
    if not oracle_raw or "answer" not in oracle_raw or "updated_at" not in oracle_raw:
        return {
            "price": 0.0,
            "updated_at": 0,
            "status": "invalid",
            "reason": "Missing oracle data"
        }
        
    try:
        raw_answer = int(oracle_raw["answer"])
        updated_at = int(oracle_raw["updated_at"])
    except (ValueError, TypeError):
        return {
            "price": 0.0,
            "updated_at": 0,
            "status": "invalid",
            "reason": "Non-integer oracle values"
        }
        
    if raw_answer <= 0:
        return {
            "price": 0.0,
            "updated_at": updated_at,
            "status": "invalid",
            "reason": "Oracle price is zero or negative"
        }
        
    # LINK/ETH has 18 decimals on Chainlink
    price = raw_answer / 1e18
    
    # Check stale status
    time_diff = current_time - updated_at
    if time_diff > ORACLE_HEARTBEAT_LIMIT or time_diff < 0:
        return {
            "price": price,
            "updated_at": updated_at,
            "status": "stale",
            "reason": f"Oracle data is stale (age: {time_diff}s)"
        }
        
    return {
        "price": price,
        "updated_at": updated_at,
        "status": "ok",
        "reason": None
    }
''',

    "tracker/recommend.py": '''# tracker/recommend.py
from tracker.thresholds import ABNORMAL_PRICE_THRESHOLD, ABOUT_THE_SAME_THRESHOLD

def generate_recommendations(cache_data, oracle_info):
    """
    Generates dynamic buy and sell recommendations comparing pools.
    """
    pools = cache_data.get("pools", [])
    
    recommendations = {
        "buy": {"winner": None, "advantage_pct": 0.0, "status": "ok", "reason": ""},
        "sell": {"winner": None, "advantage_pct": 0.0, "status": "ok", "reason": ""}
    }
    
    oracle_status = oracle_info.get("status")
    oracle_price = oracle_info.get("price", 0.0)
    
    oracle_banner = ""
    if oracle_status in ["stale", "invalid"]:
        oracle_banner = "Oracle unavailable: sanity check off"
        
    valid_pools = []
    for p in pools:
        price = p.get("price_weth_per_link", 0.0)
        
        # Hide abnormal pools if they deviate more than 5% from Chainlink
        if oracle_status == "ok" and oracle_price > 0:
            deviation = abs(price - oracle_price) / oracle_price
            if deviation > ABNORMAL_PRICE_THRESHOLD:
                p["abnormal"] = True
                continue
        p["abnormal"] = False
        valid_pools.append(p)
        
    for direction in ["buy", "sell"]:
        quote_field = "quote_buy_link_out" if direction == "buy" else "quote_sell_weth_out"
        
        candidates = []
        for p in valid_pools:
            q_val = p.get(quote_field)
            if q_val:
                try:
                    candidates.append((p["fee"], float(q_val)))
                except (ValueError, TypeError):
                    pass
                    
        if len(candidates) < 2:
            recommendations[direction] = {
                "winner": None,
                "advantage_pct": 0.0,
                "status": "error",
                "reason": "Quote unavailable or pool price abnormal"
            }
            continue
            
        candidates.sort(key=lambda x: x[1], reverse=True)
        winner_fee, winner_val = candidates[0]
        loser_fee, loser_val = candidates[1]
        
        if loser_val == 0:
            advantage = 0.0
        else:
            advantage = (winner_val - loser_val) / loser_val
            
        if advantage < ABOUT_THE_SAME_THRESHOLD:
            reason = "About the same"
            winner_name = "Tie"
        else:
            reason = f"Pool {winner_fee/10000}% gives {advantage * 100:.3f}% more"
            winner_name = f"{winner_fee/10000}%"
            
        recommendations[direction] = {
            "winner": winner_name,
            "advantage_pct": round(advantage * 100, 4),
            "status": "ok",
            "reason": reason
        }
        
        if oracle_banner:
            recommendations[direction]["oracle_warning"] = oracle_banner
            
    return recommendations
''',

    "tracker/server.py": '''# tracker/server.py
import os
import json
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from tracker.oracle import check_oracle
from tracker.recommend import generate_recommendations

app = FastAPI(title="Uniswap Live Tracker API")

CACHE_FILE = "data/cache.json"
last_mtime = 0
cached_state = None

def get_latest_state():
    global last_mtime, cached_state
    
    if not os.path.exists(CACHE_FILE):
        return {"status": "no_data"}
        
    try:
        current_mtime = os.path.getmtime(CACHE_FILE)
        if current_mtime != last_mtime or cached_state is None:
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
                
            if data.get("schema_version") != 1:
                return {"status": "unknown_schema_version"}
                
            cached_state = data
            last_mtime = current_mtime
            
        oracle_raw = cached_state.get("oracle_raw", {})
        oracle_info = check_oracle(oracle_raw)
        recs = generate_recommendations(cached_state, oracle_info)
        
        response_state = dict(cached_state)
        response_state["oracle_status"] = oracle_info
        response_state["recommendations"] = recs
        response_state["cache_status"] = "ok"
        response_state["status"] = "ready"
        
        return response_state
        
    except Exception as e:
        # Fallback to last known good cache read
        if cached_state is not None:
            response_state = dict(cached_state)
            oracle_raw = cached_state.get("oracle_raw", {})
            oracle_info = check_oracle(oracle_raw)
            recs = generate_recommendations(cached_state, oracle_info)
            response_state["oracle_status"] = oracle_info
            response_state["recommendations"] = recs
            response_state["cache_status"] = "stale_read"
            response_state["status"] = "ready"
            return response_state
        else:
            return {"status": "no_data"}

@app.get("/api/state")
def api_state():
    return JSONResponse(content=get_latest_state())

# Mount web directory to serve frontend assets
if os.path.exists("web"):
    app.mount("/", StaticFiles(directory="web", html=True), name="web")
''',

    "web/glossary.json": '''{
  "fee_tier": "The percentage of trade volume collected by liquidity providers. Lower tiers are cheaper but might have less liquidity for large trades.",
  "liquidity": "The volume of tokens available in the pool. Higher liquidity reduces price impact/slippage.",
  "price_impact": "The effect that a trade has on the price of the asset within the pool, based on size relative to pool liquidity.",
  "slippage": "The difference between the expected price of a trade and the price at which the trade actually executes.",
  "quote": "The exact output amount calculated by Uniswap v3 QuoterV2 for a specific input size.",
  "oracle": "An on-chain data feed (like Chainlink) used to verify asset prices independently.",
  "impermanent_loss": "The difference in value between holding tokens directly versus providing liquidity to a pool.",
  "tick": "The boundary unit representing a 0.01% price movement in Uniswap v3."
}''',

    "web/sample_state.json": '''{
  "schema_version": 1,
  "block": 23847120,
  "timestamp": 1764000000,
  "base_fee_wei": "8500000000",
  "rpc_status": "ok",
  "pools": [
    {
      "fee": 3000,
      "pool": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
      "sqrt_price_x96": "1771595571142957166518320255467520",
      "liquidity": "48211034417892211",
      "price_weth_per_link": 0.00512,
      "quote_buy_link_out": "58412300000000000000",
      "quote_sell_weth_out": "255100000000000000"
    },
    {
      "fee": 10000,
      "pool": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
      "sqrt_price_x96": "1773000000000000000000000000000000",
      "liquidity": "25000000000000000",
      "price_weth_per_link": 0.00515,
      "quote_buy_link_out": "58200000000000000000",
      "quote_sell_weth_out": "254000000000000000"
    }
  ],
  "oracle_raw": {"round_id": "1", "answer": "5120000000000000", "updated_at": 1763998800},
  "stats": {
    "window": "2026-11-23/2026-11-30",
    "snapshots": 2016,
    "buy": {
      "0.3%": {"win_rate": 82.0, "median_adv": 0.15, "max_adv": 1.2},
      "1.0%": {"win_rate": 18.0, "median_adv": 0.05, "max_adv": 0.8}
    },
    "sell": {
      "0.3%": {"win_rate": 78.0, "median_adv": 0.12, "max_adv": 1.1},
      "1.0%": {"win_rate": 22.0, "median_adv": 0.04, "max_adv": 0.7}
    },
    "drift": 0.0045
  }
}''',

    "web/index.html": '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Uniswap Live Tracker Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .banner { display: none; margin-bottom: 15px; }
        .winner-highlight { background-color: #d1e7dd !important; font-weight: bold; }
        .tooltip-custom { cursor: help; border-bottom: 1px dashed #6c757d; }
    </style>
</head>
<body class="bg-light">
    <div class="container my-4">
        <header class="pb-3 mb-4 border-bottom d-flex justify-content-between align-items-center">
            <span class="fs-4 fw-bold text-primary">Uniswap Live Tracker Dashboard</span>
            <span id="update-label" class="badge bg-secondary">Waiting for first poll...</span>
        </header>

        <!-- Dynamic Warning Banners -->
        <div id="banner-oracle" class="alert alert-warning banner">⚠️ Oracle unavailable: sanity check off</div>
        <div id="banner-rpc" class="alert alert-danger banner">⚠️ RPC error, showing last known prices</div>
        <div id="banner-abnormal" class="alert alert-warning banner">⚠️ Price looks abnormal (deviates from Chainlink > 5%)</div>
        <div id="banner-quote" class="alert alert-secondary banner">⚠️ Quote unavailable</div>
        <div id="banner-cache" class="alert alert-info banner">⚠️ Waiting for first poll...</div>

        <div class="row">
            <!-- Left Panel: Live Data Metrics -->
            <div class="col-lg-7">
                <div class="card mb-4 shadow-sm">
                    <div class="card-header bg-white py-3">
                        <h5 class="mb-0 text-dark">Live Quotes Table (LINK / WETH)</h5>
                    </div>
                    <div class="card-body">
                        <table class="table table-bordered align-middle">
                            <thead class="table-light">
                                <tr>
                                    <th>Pool <span class="tooltip-custom" data-term="fee_tier">Fee Tier</span></th>
                                    <th>Price (WETH/LINK)</th>
                                    <th>Buy Quote <span class="tooltip-custom" data-term="quote">(LINK out)</span></th>
                                    <th>Sell Quote <span class="tooltip-custom" data-term="quote">(WETH out)</span></th>
                                    <th><span class="tooltip-custom" data-term="liquidity">Liquidity</span></th>
                                </tr>
                            </thead>
                            <tbody id="quotes-tbody">
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="card mb-4 shadow-sm border-success">
                    <div class="card-header bg-success text-white py-3">
                        <h5 class="mb-0">Live Recommendation</h5>
                    </div>
                    <div class="card-body">
                        <p class="text-muted small">Informational only. A quote is not a guaranteed price: it can change before a trade executes.</p>
                        <div class="row">
                            <div class="col-md-6 border-end">
                                <h6>📈 BUYING LINK (Input: 0.3 WETH)</h6>
                                <p id="rec-buy-text" class="fs-5 fw-bold text-success">-</p>
                            </div>
                            <div class="col-md-6">
                                <h6>📉 SELLING LINK (Input: 50 LINK)</h6>
                                <p id="rec-sell-text" class="fs-5 fw-bold text-success">-</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Right Panel: Historical Performance & Glossary -->
            <div class="col-lg-5">
                <div class="card mb-4 shadow-sm">
                    <div class="card-header bg-white py-3">
                        <h5 class="mb-0 text-dark">📊 7-Day Statistics Panel</h5>
                    </div>
                    <div class="card-body">
                        <p class="text-muted small">Date Range: <span id="stats-window">-</span> (<span id="stats-snapshots">0</span> snapshots)</p>
                        <h6 class="fw-bold text-secondary">Buying LINK Preference</h6>
                        <ul class="list-unstyled ps-2">
                            <li>0.3% Pool Win Rate: <span id="stats-buy-03-win">-</span></li>
                            <li>1.0% Pool Win Rate: <span id="stats-buy-10-win">-</span></li>
                            <li>Median Winning Advantage: <span id="stats-buy-med-adv">-</span></li>
                        </ul>
                        <h6 class="fw-bold text-secondary mt-3">Selling LINK Preference</h6>
                        <ul class="list-unstyled ps-2">
                            <li>0.3% Pool Win Rate: <span id="stats-sell-03-win">-</span></li>
                            <li>1.0% Pool Win Rate: <span id="stats-sell-10-win">-</span></li>
                            <li>Median Winning Advantage: <span id="stats-sell-med-adv">-</span></li>
                        </ul>
                        <p class="small text-muted mt-2">Pool Price Drift: <span id="stats-drift">-</span></p>
                    </div>
                </div>

                <div class="card mb-4 shadow-sm">
                    <div class="card-header bg-white py-3">
                        <h5 class="mb-0 text-dark">📖 Glossary Explanation</h5>
                    </div>
                    <div class="card-body" id="glossary-section">
                        <p class="text-muted">Hover over terms with dashed underlines to see definitions here.</p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="app.js"></script>
</body>
</html>''',

    "web/app.js": '''// app.js

let glossary = {};

async function loadGlossary() {
    try {
        const res = await fetch('glossary.json');
        glossary = await res.json();
    } catch (e) {
        console.error('Failed to load glossary', e);
    }
}

async function fetchState() {
    try {
        const res = await fetch('/api/state');
        const state = await res.json();
        updateUI(state);
    } catch (e) {
        console.error('Failed to fetch state', e);
        document.getElementById('banner-cache').style.display = 'block';
        document.getElementById('banner-cache').innerText = '⚠️ Error: Unable to contact backend server.';
    }
}

function updateUI(state) {
    if (state.status === 'no_data') {
        document.getElementById('banner-cache').style.display = 'block';
        document.getElementById('banner-cache').innerText = '⚠️ Waiting for first poll...';
        return;
    } else {
        document.getElementById('banner-cache').style.display = 'none';
    }

    const timestamp = state.timestamp * 1000;
    const dateStr = new Date(timestamp).toLocaleTimeString();
    document.getElementById('update-label').innerText = `Last updated: ${dateStr}`;

    const oracleStatus = state.oracle_status?.status;
    document.getElementById('banner-oracle').style.display = (oracleStatus === 'stale' || oracleStatus === 'invalid') ? 'block' : 'none';
    document.getElementById('banner-rpc').style.display = (state.rpc_status === 'error') ? 'block' : 'none';
    
    let hasAbnormal = false;

    const tbody = document.getElementById('quotes-tbody');
    tbody.innerHTML = '';
    
    const recs = state.recommendations || {};
    const buyWinner = recs.buy?.winner;
    const sellWinner = recs.sell?.winner;

    state.pools.forEach(pool => {
        const row = document.createElement('tr');
        const feePercent = (pool.fee / 10000).toFixed(2) + '%';
        
        if (pool.abnormal) hasAbnormal = true;

        const buyClass = (buyWinner === feePercent) ? 'winner-highlight' : '';
        const sellClass = (sellWinner === feePercent) ? 'winner-highlight' : '';

        row.innerHTML = `
            <td><strong>${feePercent}</strong></td>
            <td>${pool.price_weth_per_link?.toFixed(6) || '-'}</td>
            <td class="${buyClass}">${pool.quote_buy_link_out ? (parseFloat(pool.quote_buy_link_out)/1e18).toFixed(4) : 'N/A'}</td>
            <td class="${sellClass}">${pool.quote_sell_weth_out ? (parseFloat(pool.quote_sell_weth_out)/1e18).toFixed(4) : 'N/A'}</td>
            <td>${parseFloat(pool.liquidity).toLocaleString()}</td>
        `;
        tbody.appendChild(row);
    });

    document.getElementById('banner-abnormal').style.display = hasAbnormal ? 'block' : 'none';

    if (recs.buy) {
        document.getElementById('rec-buy-text').innerText = recs.buy.reason || recs.buy.status;
    }
    if (recs.sell) {
        document.getElementById('rec-sell-text').innerText = recs.sell.reason || recs.sell.status;
    }

    if (state.stats) {
        document.getElementById('stats-window').innerText = state.stats.window || '-';
        document.getElementById('stats-snapshots').innerText = state.stats.snapshots || '0';
        
        if (state.stats.buy) {
            document.getElementById('stats-buy-03-win').innerText = (state.stats.buy['0.3%']?.win_rate || 0) + '%';
            document.getElementById('stats-buy-10-win').innerText = (state.stats.buy['1.0%']?.win_rate || 0) + '%';
            document.getElementById('stats-buy-med-adv').innerText = (state.stats.buy['0.3%']?.median_adv || 0) + '%';
        }
        if (state.stats.sell) {
            document.getElementById('stats-sell-03-win').innerText = (state.stats.sell['0.3%']?.win_rate || 0) + '%';
            document.getElementById('stats-sell-10-win').innerText = (state.stats.sell['1.0%']?.win_rate || 0) + '%';
            document.getElementById('stats-sell-med-adv').innerText = (state.stats.sell['0.3%']?.median_adv || 0) + '%';
        }
        document.getElementById('stats-drift').innerText = (state.stats.drift * 100).toFixed(2) + '%';
    }
}

document.body.addEventListener('mouseover', (e) => {
    if (e.target.classList.contains('tooltip-custom')) {
        const term = e.target.getAttribute('data-term');
        const desc = glossary[term] || "No description found.";
        document.getElementById('glossary-section').innerHTML = `<strong>${term.replace('_', ' ').toUpperCase()}:</strong> ${desc}`;
    }
});

loadGlossary().then(() => {
    fetchState();
    setInterval(fetchState, 30000);
});
''',

    "tests/test_oracle.py": '''# tests/test_oracle.py
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tracker.oracle import check_oracle

def test_oracle_valid():
    raw = {"round_id": "1", "answer": "5120000000000000", "updated_at": 1000}
    res = check_oracle(raw, current_time=1001)
    assert res["status"] == "ok"
    assert res["price"] == 0.00512

def test_oracle_stale():
    raw = {"round_id": "1", "answer": "5120000000000000", "updated_at": 1000}
    res = check_oracle(raw, current_time=5000)
    assert res["status"] == "stale"

def test_oracle_invalid():
    raw = {"round_id": "1", "answer": "-100", "updated_at": 1000}
    res = check_oracle(raw, current_time=1001)
    assert res["status"] == "invalid"
''',

    "tests/test_recommend.py": '''# tests/test_recommend.py
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tracker.recommend import generate_recommendations

def test_recommend_buy_and_sell():
    cache_data = {
        "pools": [
            {
                "fee": 3000,
                "price_weth_per_link": 0.00512,
                "quote_buy_link_out": "100000000000000000",
                "quote_sell_weth_out": "200000000000000000"
            },
            {
                "fee": 10000,
                "price_weth_per_link": 0.00515,
                "quote_buy_link_out": "99000000000000000",
                "quote_sell_weth_out": "210000000000000000"
            }
        ]
    }
    oracle_info = {"status": "ok", "price": 0.00512}
    
    recs = generate_recommendations(cache_data, oracle_info)
    assert recs["buy"]["winner"] == "0.3%"
    assert recs["sell"]["winner"] == "1.0%"
'''
}

# Create dirs and write out each file cleanly
print("--- Launching Project Construction Engine ---")
for file_path, code in project_files.items():
    directory = os.path.dirname(file_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(code)
    print(f"Created file: {file_path}")

print("\\n✅ Member B files structured successfully! Enjoy developing!")
