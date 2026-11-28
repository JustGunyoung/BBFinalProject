"""JSON-RPC access: one real client, plus a recorder and a replayer for tests.

All three classes share the same five methods, so the rest of the code does not
care which one it gets. Every failure becomes RpcError with the API key removed,
because the key is part of the URL and network errors like to quote the URL.
"""

import json
import os
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from web3 import HTTPProvider, Web3

from tracker import config


class RpcError(Exception):
    """An RPC request failed. The message never contains the API key."""

    def __init__(self, message: str):
        super().__init__(message)
        lowered = message.lower()
        # Callers back off on this one instead of failing straight away.
        self.rate_limited = "429" in lowered or "too many requests" in lowered


class Rpc:
    """Real client over HTTP. `calls` counts requests by method for the CU log."""

    def __init__(self, base_url: str, api_key: str):
        self._api_key = api_key
        url = f"{base_url.rstrip('/')}/{api_key}"
        self._w3 = Web3(HTTPProvider(url, request_kwargs={"timeout": 30}))
        self.calls: Counter = Counter()

    @classmethod
    def from_env(cls) -> "Rpc":
        load_dotenv(config.PROJECT_ROOT / ".env")
        base_url = os.getenv("RPC_BASE_URL", "").strip()
        api_key = os.getenv("API_KEY", "").strip()
        if not base_url or not api_key:
            raise RpcError("Missing API key: open .env and paste your key after API_KEY=")
        return cls(base_url, api_key)

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "<API_KEY>") if self._api_key else text

    def _request(self, method: str, fn):
        self.calls[method] += 1
        try:
            return fn()
        except Exception as exc:
            message = f"{type(exc).__name__}: {self._redact(str(exc))}"
            raise RpcError(message) from None  # `from None`: the cause can quote the URL

    def chain_id(self) -> int:
        return self._request("chain_id", lambda: int(self._w3.eth.chain_id))

    def latest_block_number(self) -> int:
        return self._request("block_number", lambda: int(self._w3.eth.block_number))

    def block(self, n: int) -> dict:
        """Number, timestamp and base fee (None before the London fork)."""

        def fetch():
            b = self._w3.eth.get_block(n)
            fee = b.get("baseFeePerGas")
            return {
                "number": int(b["number"]),
                "timestamp": int(b["timestamp"]),
                "base_fee": None if fee is None else int(fee),
            }

        return self._request("get_block", fetch)

    def eth_call(self, to: str, data: bytes, block: int) -> bytes:
        """Run a read-only call as of `block` and return the raw result."""

        def fetch():
            tx = {"to": Web3.to_checksum_address(to), "data": data}
            return bytes(self._w3.eth.call(tx, block_identifier=block))

        return self._request("eth_call", fetch)


def _call_key(to: str, data: bytes, block: int) -> str:
    return f"{to.lower()}|0x{bytes(data).hex()}|{block}"


class RecordingRpc:
    """Wraps another client and remembers every answer, errors included."""

    def __init__(self, inner):
        self.inner = inner
        self.record = {"chain_id": None, "latest": None, "blocks": {}, "calls": {}}

    @property
    def calls(self):
        return self.inner.calls

    def chain_id(self) -> int:
        self.record["chain_id"] = self.inner.chain_id()
        return self.record["chain_id"]

    def latest_block_number(self) -> int:
        self.record["latest"] = self.inner.latest_block_number()
        return self.record["latest"]

    def block(self, n: int) -> dict:
        info = self.inner.block(n)
        self.record["blocks"][str(n)] = info
        return info

    def eth_call(self, to: str, data: bytes, block: int) -> bytes:
        key = _call_key(to, data, block)
        try:
            result = self.inner.eth_call(to, data, block)
        except RpcError as exc:
            self.record["calls"][key] = {"error": str(exc)}
            raise
        self.record["calls"][key] = "0x" + result.hex()
        return result

    def dump(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.record, indent=2), encoding="utf-8")


class FixtureRpc:
    """Replays a recording. Anything that was not recorded raises RpcError."""

    def __init__(self, path_or_dict):
        if isinstance(path_or_dict, dict):
            self.fixture = path_or_dict
        else:
            self.fixture = json.loads(Path(path_or_dict).read_text(encoding="utf-8"))
        self.calls: Counter = Counter()

    def chain_id(self) -> int:
        self.calls["chain_id"] += 1
        if self.fixture.get("chain_id") is None:
            raise RpcError("fixture has no chain_id")
        return int(self.fixture["chain_id"])

    def latest_block_number(self) -> int:
        self.calls["block_number"] += 1
        if self.fixture.get("latest") is None:
            raise RpcError("fixture has no latest block")
        return int(self.fixture["latest"])

    def block(self, n: int) -> dict:
        self.calls["get_block"] += 1
        info = self.fixture.get("blocks", {}).get(str(n))
        if info is None:
            raise RpcError(f"fixture has no block {n}")
        return dict(info)

    def eth_call(self, to: str, data: bytes, block: int) -> bytes:
        self.calls["eth_call"] += 1
        entry = self.fixture.get("calls", {}).get(_call_key(to, data, block))
        if entry is None:
            raise RpcError(f"fixture has no eth_call for {to} at block {block}")
        if isinstance(entry, dict):
            raise RpcError(entry.get("error", "recorded error"))
        return bytes.fromhex(entry[2:])
