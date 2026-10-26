"""Write balances as CSV (for merkle-airdrop) or JSON."""

from __future__ import annotations

import csv
import io
import json


def ordered(balances: dict[str, int]) -> list[tuple[str, int]]:
    """Largest first, ties broken by address so output is deterministic."""
    return sorted(balances.items(), key=lambda kv: (-kv[1], kv[0]))


def to_csv(balances: dict[str, int]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["address", "amount"])
    for address, amount in ordered(balances):
        writer.writerow([address, amount])
    return out.getvalue()


def to_json(balances: dict[str, int], *, token: str, block: int, stats: dict | None = None) -> str:
    doc = {
        "token": token,
        "block": block,
        "holders": [{"address": a, "amount": str(v)} for a, v in ordered(balances)],
    }
    if stats is not None:
        doc["stats"] = stats
    return json.dumps(doc, indent=2) + "\n"
