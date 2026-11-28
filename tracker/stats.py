"""Rolling statistics over the snapshot history (standard library only).

Plain-language definitions
--------------------------
Window
    Look at the newest usable snapshot's timestamp.  The window holds every
    usable snapshot whose timestamp is at least ``days * 86400`` seconds
    before it.  ``window`` is the string ``"YYYY-MM-DD/YYYY-MM-DD"`` (UTC
    dates of the first and last snapshot in the window).

Usable snapshot
    Snapshots with ``rpc_status == "error"`` are dropped entirely, because the
    poller repeats old values in them.  Input may be unsorted (it is sorted by
    block) and may contain duplicate blocks (the last one given is kept).

Direction (``buy`` / ``sell``)
    ``buy`` compares ``quote_buy_link_out`` (LINK received), ``sell`` compares
    ``quote_sell_weth_out`` (WETH received).  A snapshot is *compared* in a
    direction only if both pools have a non-null quote for it.  The pool with
    the larger output wins.  Equal outputs are a tie (counted under
    ``"tie"``, no advantage recorded).  Quotes are big-int strings and are
    compared as Python ints, never floats.

Advantage
    ``(winner_out - loser_out) / loser_out``, computed exactly with
    ``Fraction`` and then converted to a float.  ``median_advantage`` and
    ``max_advantage`` are taken over the non-tie comparisons (null if there
    are none).  If the losing quote is 0 the win is counted but no advantage
    is recorded (it would be infinite).

Win rate
    ``wins[fee] / compared`` per fee tier (0.0 when nothing was compared).
    The tie share is ``wins["tie"] / compared``, so win rates plus the tie
    share add up to 1 whenever ``compared > 0``.

Drift
    Compared only on snapshots where both pools have ``price_check == "ok"``
    (or no such field) and a positive price.  A snapshot "drifted" when
    ``|p1 - p2| / min(p1, p2) > threshold`` (strictly greater).  ``rate`` is
    drifted / compared, or null when compared is 0.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from fractions import Fraction

_DAY = 86400


def _quote(pool: dict, key: str):
    v = pool.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _price(pool: dict):
    if pool.get("price_check", "ok") != "ok":
        return None
    p = pool.get("price_weth_per_link")
    if p is None or isinstance(p, bool):
        return None
    try:
        f = Fraction(repr(float(p)))
    except (TypeError, ValueError, OverflowError):
        return None
    return f if f > 0 else None


def _by_fee(snap: dict) -> dict:
    return {int(p["fee"]): p for p in snap.get("pools", [])}


def _utc_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _direction(window: list, fees: list, key: str) -> dict:
    labels = [str(f) for f in fees]
    wins = {lab: 0 for lab in labels}
    wins["tie"] = 0
    compared = 0
    advantages: list = []
    if len(fees) == 2:
        for snap in window:
            pools = _by_fee(snap)
            if fees[0] not in pools or fees[1] not in pools:
                continue
            a = _quote(pools[fees[0]], key)
            b = _quote(pools[fees[1]], key)
            if a is None or b is None:
                continue
            compared += 1
            if a == b:
                wins["tie"] += 1
                continue
            win_i, win_v, lose_v = (0, a, b) if a > b else (1, b, a)
            wins[labels[win_i]] += 1
            if lose_v > 0:
                advantages.append(float(Fraction(win_v - lose_v, lose_v)))
    win_rate = {lab: (wins[lab] / compared if compared else 0.0) for lab in labels}
    return {
        "compared": compared,
        "wins": wins,
        "win_rate": win_rate,
        "median_advantage": statistics.median(advantages) if advantages else None,
        "max_advantage": max(advantages) if advantages else None,
    }


def compute_stats(history, days=7, drift_threshold=0.005, fee_tiers=None) -> dict:
    # Drop error snapshots, dedupe by block (last wins), sort by block.
    by_block = {}
    for s in history:
        if s.get("rpc_status") == "error":
            continue
        by_block[s["block"]] = s
    snaps = [by_block[b] for b in sorted(by_block)]

    if fee_tiers is None:
        fee_tiers = [int(p["fee"]) for p in snaps[-1].get("pools", [])] if snaps else []
    fees = [int(f) for f in fee_tiers]
    if len(fees) not in (0, 2):
        raise ValueError("fee_tiers must contain exactly two fee tiers")

    if snaps:
        cutoff = max(s["timestamp"] for s in snaps) - days * _DAY
        window = [s for s in snaps if s["timestamp"] >= cutoff]
        start_ts = min(s["timestamp"] for s in window)
        end_ts = max(s["timestamp"] for s in window)
        window_str = f"{_utc_date(start_ts)}/{_utc_date(end_ts)}"
    else:
        window, start_ts, end_ts, window_str = [], None, None, None

    thr = Fraction(str(drift_threshold))
    d_compared = d_count = 0
    if len(fees) == 2:
        for s in window:
            pools = _by_fee(s)
            if fees[0] not in pools or fees[1] not in pools:
                continue
            p1, p2 = _price(pools[fees[0]]), _price(pools[fees[1]])
            if p1 is None or p2 is None:
                continue
            d_compared += 1
            if abs(p1 - p2) / min(p1, p2) > thr:
                d_count += 1

    return {
        "window": window_str,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "snapshots": len(window),
        "drift": {
            "threshold": drift_threshold,
            "compared": d_compared,
            "rate": d_count / d_compared if d_compared else None,
        },
        "buy": _direction(window, fees, "quote_buy_link_out"),
        "sell": _direction(window, fees, "quote_sell_weth_out"),
    }
