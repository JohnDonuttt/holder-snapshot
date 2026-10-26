"""Distribution statistics for a set of balances."""

from __future__ import annotations

from dataclasses import dataclass


def gini(values: list[int]) -> float:
    """Gini coefficient: 0 means everyone holds the same, values near 1 mean one holder has it all.

    For n holders the maximum is (n - 1) / n, which is reached when a single holder has everything.
    """
    n = len(values)
    total = sum(values)
    if n == 0 or total == 0:
        return 0.0

    ordered = sorted(values)
    weighted = sum(rank * value for rank, value in enumerate(ordered, start=1))
    return (2 * weighted) / (n * total) - (n + 1) / n


def top_share(values: list[int], n: int) -> float:
    """Fraction of the total held by the n largest holders."""
    total = sum(values)
    if total == 0:
        return 0.0
    return sum(sorted(values, reverse=True)[:n]) / total


@dataclass(frozen=True)
class Summary:
    holders: int
    total: int
    top1_share: float
    top10_share: float
    gini: float

    def as_dict(self) -> dict:
        return {
            "holders": self.holders,
            "total": str(self.total),
            "top1Share": round(self.top1_share, 6),
            "top10Share": round(self.top10_share, 6),
            "gini": round(self.gini, 6),
        }


def summarize(balances: dict[str, int]) -> Summary:
    values = list(balances.values())
    return Summary(
        holders=len(values),
        total=sum(values),
        top1_share=top_share(values, 1),
        top10_share=top_share(values, 10),
        gini=gini(values),
    )
