"""Shared fakes and builders for the tests."""

from __future__ import annotations

from holder_snapshot.balances import TRANSFER_TOPIC, ZERO_ADDRESS
from holder_snapshot.rpc import RpcError


def addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def topic(address: str) -> str:
    return "0x" + address.removeprefix("0x").rjust(64, "0")


def transfer_log(sender: str, recipient: str, amount: int, block: int, index: int = 0) -> dict:
    return {
        "topics": [TRANSFER_TOPIC, topic(sender), topic(recipient)],
        "data": "0x" + f"{amount:064x}",
        "blockNumber": hex(block),
        "logIndex": hex(index),
    }


def mint(to: str, amount: int, block: int, index: int = 0) -> dict:
    return transfer_log(ZERO_ADDRESS, to, amount, block, index)


class FakeClient:
    """Stands in for RpcClient: serves canned logs and answers balanceOf from them."""

    def __init__(self, logs: list[dict], head: int = 100, balance_overrides: dict | None = None):
        self.logs = logs
        self.head = head
        self.overrides = balance_overrides or {}
        self.log_requests: list[tuple[int, int]] = []
        self.max_range: int | None = None  # simulate a provider that rejects wide ranges

    def block_number(self) -> int:
        return self.head

    def get_logs(self, address: str, topics: list, from_block: int, to_block: int) -> list[dict]:
        self.log_requests.append((from_block, to_block))
        if self.max_range is not None and to_block - from_block + 1 > self.max_range:
            raise RpcError(-32000, "query returned more than 10000 results")
        return [log for log in self.logs if from_block <= int(log["blockNumber"], 16) <= to_block]

    def call_contract(self, to: str, data: str, block: int) -> str:
        holder = "0x" + data[-40:]
        if holder in self.overrides:
            return hex(self.overrides[holder])

        from holder_snapshot.balances import replay

        upto = [log for log in self.logs if int(log["blockNumber"], 16) <= block]
        return hex(replay(upto).holders.get(holder, 0))
