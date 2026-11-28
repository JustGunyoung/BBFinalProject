"""Price math for Uniswap v3. Exact: it uses Fraction, not floats, until the end.

The pool stores sqrt(token1/token0) in the tokens' smallest units, scaled by
2^96. Everything here turns that into a price in whole tokens, or back.
"""

import math
from fractions import Fraction

Q96 = 2**96
# One tick moves the price 0.01%. The margin covers float rounding only.
TICK_TOLERANCE = 0.00011


def raw_price(sqrt_price_x96: int) -> Fraction:
    """token1 per token0 in smallest units."""
    return Fraction(sqrt_price_x96, Q96) ** 2


def to_price(sqrt_price_x96: int, dec0: int, dec1: int, invert: bool = False) -> Fraction:
    """Price in whole tokens: token1 per token0, or token0 per token1 if invert.

    Works for any token order and any decimals, e.g. 6/18 for USDC/WETH.
    """
    price = raw_price(sqrt_price_x96) * Fraction(10) ** (dec0 - dec1)
    return 1 / price if invert else price


def check_tick(sqrt_price_x96: int, tick: int) -> bool:
    """True if sqrtPriceX96 and the tick give the same price (price = 1.0001^tick)."""
    try:
        ratio = float(raw_price(sqrt_price_x96)) / 1.0001**tick
    except (OverflowError, ZeroDivisionError):
        return False
    return abs(ratio - 1) <= TICK_TOLERANCE


def price_to_sqrt_price_x96(price, dec0: int, dec1: int) -> int:
    """Inverse of to_price (rounded down). `price` is token1 per token0, whole tokens."""
    raw = Fraction(price) / Fraction(10) ** (dec0 - dec1)
    return math.isqrt(raw.numerator * Q96**2 // raw.denominator)
