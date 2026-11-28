# tracker/server.py
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
