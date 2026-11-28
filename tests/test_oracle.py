# tests/test_oracle.py
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
