"""Config sanity: addresses are checksummed and the fee tiers are known."""

import pytest
from web3 import Web3

from tracker import config

ADDRESSES = [
    "FACTORY", "QUOTER_V2", "MULTICALL3", "LINK", "WETH", "CHAINLINK_LINK_ETH",
]


@pytest.mark.parametrize("name", ADDRESSES)
def test_address_is_checksummed(name):
    address = getattr(config, name)
    assert Web3.to_checksum_address(address) == address


def test_fee_tiers_have_spacings():
    assert set(config.FEE_TIERS) <= set(config.CANDIDATE_FEE_TIERS)
    assert set(config.TICK_SPACINGS) == set(config.CANDIDATE_FEE_TIERS)


def test_token0_sorts_before_token1():
    assert int(config.LINK, 16) < int(config.WETH, 16)
