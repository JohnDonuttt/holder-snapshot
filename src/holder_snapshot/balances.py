"""Turn a stream of ERC-20 Transfer logs into balances."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS = "0x" + "00" * 20
BALANCE_OF_SELECTOR = "0x70a08231"


class InconsistentHistory(Exception):
    """A balance would go negative, so the logs we have can't be the token's full history."""


@dataclass(frozen=True)
class Transfer:
    sender: str
    recipient: str
    amount: int
    block: int
    log_index: int


@dataclass
class Balances:
    holders: dict[str, int] = field(default_factory=dict)
    minted: int = 0
    burned: int = 0
    transfers: int = 0

    @property
    def supply(self) -> int:
        """Total supply implied by the events: everything minted minus everything burned."""
        return self.minted - self.burned


def _hex_to_int(value: Any) -> int:
    return int(value, 16) if isinstance(value, str) else int(value)


def _topic_to_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def decode_transfer(log: dict) -> Transfer | None:
    """Decode an ERC-20 Transfer log. Returns None for logs that aren't one.

    ERC-721 uses the identical event signature but indexes the token id as a fourth topic,
    so a log with anything other than 3 topics is not an ERC-20 transfer.
    """
    topics = log.get("topics", [])
    if len(topics) != 3 or topics[0].lower() != TRANSFER_TOPIC:
        return None

    data = log.get("data", "0x")
    if len(data) < 66:
        return None

    return Transfer(
        sender=_topic_to_address(topics[1]),
        recipient=_topic_to_address(topics[2]),
        amount=int(data[:66], 16),
        block=_hex_to_int(log["blockNumber"]),
        log_index=_hex_to_int(log["logIndex"]),
    )


def replay(logs: Iterable[dict]) -> Balances:
    """Apply transfers in chain order and return the resulting balances.

    Raises InconsistentHistory if any address would spend more than it holds. That means the
    logs start after the token was deployed, are incomplete, or the token isn't a plain ERC-20
    (for example, a rebasing token).
    """
    transfers = [t for t in map(decode_transfer, logs) if t is not None]
    transfers.sort(key=lambda t: (t.block, t.log_index))

    result = Balances()
    holders = result.holders

    for t in transfers:
        result.transfers += 1

        if t.sender == ZERO_ADDRESS:
            result.minted += t.amount
        else:
            current = holders.get(t.sender, 0)
            if t.amount > current:
                raise InconsistentHistory(
                    f"{t.sender} sent {t.amount} at block {t.block} but only held {current}. "
                    "Start from the block the token was deployed, or the token may not be a "
                    "standard ERC-20."
                )
            holders[t.sender] = current - t.amount

        if t.recipient == ZERO_ADDRESS:
            result.burned += t.amount
        else:
            holders[t.recipient] = holders.get(t.recipient, 0) + t.amount

    # keep only addresses that still hold something
    result.holders = {addr: bal for addr, bal in holders.items() if bal > 0}
    return result


@dataclass(frozen=True)
class Mismatch:
    address: str
    from_events: int
    on_chain: int


def verify_against_chain(
    balance_of: Callable[[str], int],
    balances: dict[str, int],
    sample: int,
) -> list[Mismatch]:
    """Check the largest holders against the contract's own balanceOf.

    Replaying events is only right for tokens whose balances change exclusively through Transfer
    events. Fee-on-transfer, rebasing and reflection tokens break that, and this is how you
    find out.
    """
    top = sorted(balances.items(), key=lambda kv: (-kv[1], kv[0]))[:sample]
    mismatches = []
    for address, from_events in top:
        on_chain = balance_of(address)
        if on_chain != from_events:
            mismatches.append(Mismatch(address, from_events, on_chain))
    return mismatches


def balance_of_calldata(address: str) -> str:
    return BALANCE_OF_SELECTOR + address.removeprefix("0x").lower().rjust(64, "0")
