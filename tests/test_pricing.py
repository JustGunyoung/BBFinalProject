"""Price math: known values, reversed order, odd decimals, tick check, round trip."""

import math
from fractions import Fraction

import pytest
from hypothesis import given, strategies as st

from tracker.pricing import (
    Q96,
    check_tick,
    price_to_sqrt_price_x96,
    raw_price,
    to_price,
)


def tick_of(sqrt):
    return math.floor(math.log(float(raw_price(sqrt))) / math.log(1.0001))


def test_link_weth_known_value():
    # sqrt = 2^92 means sqrt(price) = 1/16, so price = 1/256 exactly (hand-computed).
    sqrt = 2**92
    assert to_price(sqrt, 18, 18) == Fraction(1, 256)
    assert float(to_price(sqrt, 18, 18)) == 0.00390625


def test_price_of_one_and_four():
    assert to_price(Q96, 18, 18) == 1
    assert to_price(2 * Q96, 18, 18) == 4


def test_reversed_order_with_invert():
    # Same pool read the other way round: LINK per WETH is 256.
    assert to_price(2**92, 18, 18, invert=True) == 256


def test_usdc_weth_6_and_18_decimals():
    # USDC (6) is token0, WETH (18) is token1. 1 WETH = 2000 USDC, so
    # WETH per USDC = 1/2000 in whole tokens; in raw units that is 5e8.
    sqrt = math.isqrt(5 * 10**8 * Q96**2)
    weth_per_usdc = to_price(sqrt, 6, 18)
    assert float(weth_per_usdc) == pytest.approx(1 / 2000, rel=1e-12)
    assert float(to_price(sqrt, 6, 18, invert=True)) == pytest.approx(2000, rel=1e-12)


def test_check_tick_agrees():
    sqrt = 2**92
    assert check_tick(sqrt, tick_of(sqrt))


def test_check_tick_disagrees():
    sqrt = 2**92
    assert not check_tick(sqrt, tick_of(sqrt) + 5)
    assert not check_tick(sqrt, tick_of(sqrt) - 5)
    assert not check_tick(0, 0)


@given(
    price=st.fractions(min_value=Fraction(1, 10**6), max_value=10**6),
    dec0=st.sampled_from([6, 8, 18]),
    dec1=st.sampled_from([6, 8, 18]),
)
def test_round_trip_price_sqrt_price(price, dec0, dec1):
    sqrt = price_to_sqrt_price_x96(price, dec0, dec1)
    back = to_price(sqrt, dec0, dec1)
    assert abs(float(back) - float(price)) <= 1e-12 * float(price)
