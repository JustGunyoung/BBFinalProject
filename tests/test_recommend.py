# tests/test_recommend.py
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
