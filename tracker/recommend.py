# tracker/recommend.py
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
