# tracker/oracle.py
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
