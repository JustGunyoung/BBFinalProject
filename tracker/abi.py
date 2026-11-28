"""The ABI pieces the tracker uses, with encoding and decoding by hand.

Calls go through Multicall3, so the raw call data has to be built here rather
than by web3 contract objects. Each Fn knows its signature, 4-byte selector and
how to encode its arguments and decode its result.
"""

from dataclasses import dataclass

from eth_abi import decode, encode
from web3 import Web3


def _view(name, inputs=(), outputs=()):
    """Build an ABI entry. Every entry is marked view, so this file has no
    function that could change on-chain state."""
    return {
        "type": "function",
        "name": name,
        "stateMutability": "view",
        "inputs": [{"name": "", "type": t} for t in inputs],
        "outputs": [{"name": "", "type": t} for t in outputs],
    }


@dataclass(frozen=True)
class Fn:
    """One contract function: name, input types and output types."""

    name: str
    inputs: tuple = ()
    outputs: tuple = ()

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(self.inputs)})"

    @property
    def selector(self) -> bytes:
        return bytes(Web3.keccak(text=self.signature)[:4])

    @property
    def abi(self) -> dict:
        return _view(self.name, self.inputs, self.outputs)

    def encode(self, *args) -> bytes:
        """Call data: selector followed by the ABI-encoded arguments."""
        return self.selector + encode(list(self.inputs), args)

    def decode(self, data: bytes) -> tuple:
        """Decode a return value into a tuple. Raises if the data is malformed.

        eth_abi returns addresses in lower case; they are checksummed here so
        they compare equal to the addresses in config."""
        values = decode(list(self.outputs), bytes(data))
        return tuple(
            Web3.to_checksum_address(v) if t == "address" else v
            for t, v in zip(self.outputs, values)
        )

    def encode_result(self, *values) -> bytes:
        """ABI-encode a return value (used to build fake responses in tests)."""
        return encode(list(self.outputs), values)


def _fn(name, inputs=(), outputs=()) -> Fn:
    return Fn(name, tuple(inputs), tuple(outputs))


GET_POOL = _fn("getPool", ["address", "address", "uint24"], ["address"])

POOL_FACTORY = _fn("factory", outputs=["address"])
POOL_TOKEN0 = _fn("token0", outputs=["address"])
POOL_TOKEN1 = _fn("token1", outputs=["address"])
POOL_FEE = _fn("fee", outputs=["uint24"])
POOL_TICK_SPACING = _fn("tickSpacing", outputs=["int24"])
POOL_LIQUIDITY = _fn("liquidity", outputs=["uint128"])
POOL_SLOT0 = _fn(
    "slot0",
    outputs=["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"],
)

ERC20_SYMBOL = _fn("symbol", outputs=["string"])
ERC20_DECIMALS = _fn("decimals", outputs=["uint8"])

# Params: (tokenIn, tokenOut, amountIn, fee, sqrtPriceLimitX96). QuoterV2 is not
# marked view on-chain, but eth_call only simulates it, so nothing is changed.
QUOTE_EXACT_INPUT_SINGLE = _fn(
    "quoteExactInputSingle",
    ["(address,address,uint256,uint24,uint160)"],
    ["uint256", "uint160", "uint32", "uint256"],
)

AGGREGATE3 = _fn("aggregate3", ["(address,bool,bytes)[]"], ["(bool,bytes)[]"])

CHAINLINK_LATEST_ROUND_DATA = _fn(
    "latestRoundData",
    outputs=["uint80", "int256", "uint256", "uint256", "uint80"],
)
CHAINLINK_DECIMALS = _fn("decimals", outputs=["uint8"])
CHAINLINK_DESCRIPTION = _fn("description", outputs=["string"])
