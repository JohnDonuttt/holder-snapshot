from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence

from holder_snapshot import output
from holder_snapshot.balances import (
    TRANSFER_TOPIC,
    InconsistentHistory,
    balance_of_calldata,
    replay,
    verify_against_chain,
)
from holder_snapshot.rpc import RpcClient, RpcError, TransportError, fetch_logs_adaptive
from holder_snapshot.stats import summarize

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def address_arg(value: str) -> str:
    if not ADDRESS_RE.match(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a 0x-prefixed 40-character address")
    return value.lower()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="holder-snapshot",
        description="Rebuild who holds an ERC-20 token at a block, from its Transfer events.",
    )
    p.add_argument("token", type=address_arg, help="token contract address")
    p.add_argument("--rpc", default=os.environ.get("RPC_URL"), help="JSON-RPC URL (or set RPC_URL)")
    p.add_argument(
        "--from-block",
        type=int,
        default=0,
        help="first block to scan; use the token's deployment block (default 0)",
    )
    p.add_argument("--block", type=int, help="snapshot block (default: latest)")
    p.add_argument(
        "--min-balance", type=int, default=1, help="drop holders below this (base units)"
    )
    p.add_argument(
        "--exclude",
        type=address_arg,
        nargs="*",
        default=[],
        metavar="ADDRESS",
        help="addresses to leave out, such as liquidity pools or the treasury",
    )
    p.add_argument("--top", type=int, help="keep only the N largest holders")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.add_argument("-o", "--output", help="write here instead of stdout")
    p.add_argument("--stats", action="store_true", help="print distribution stats to stderr")
    p.add_argument(
        "--verify",
        type=int,
        default=10,
        metavar="N",
        help="check the N largest balances against balanceOf at the snapshot block (default 10)",
    )
    p.add_argument("--skip-verify", action="store_true", help="don't check against balanceOf")
    p.add_argument("--chunk", type=int, default=50_000, help="starting block range per request")
    return p


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def main(argv: Sequence[str] | None = None, client: RpcClient | None = None) -> int:
    args = build_parser().parse_args(argv)

    if client is None:
        if not args.rpc:
            _log("error: no RPC endpoint: pass --rpc <url> or set RPC_URL")
            return 1
        client = RpcClient(args.rpc)

    try:
        return _run(args, client)
    except InconsistentHistory as exc:
        _log(f"error: {exc}")
        return 1
    except (RpcError, TransportError) as exc:
        _log(f"error: {exc}")
        return 1


def _run(args: argparse.Namespace, client: RpcClient) -> int:
    block = args.block if args.block is not None else client.block_number()
    if args.from_block > block:
        _log(f"error: --from-block {args.from_block} is after the snapshot block {block}")
        return 1

    show_progress = sys.stderr.isatty()

    def progress(done: int, total: int) -> None:
        if show_progress:
            print(f"\rscanning blocks... {done * 100 // total}%", end="", file=sys.stderr)

    logs = fetch_logs_adaptive(
        lambda lo, hi: client.get_logs(args.token, [TRANSFER_TOPIC], lo, hi),
        args.from_block,
        block,
        args.chunk,
        progress,
    )
    if show_progress:
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr)

    result = replay(logs)
    _log(f"snapshot at block {block}: {result.transfers} transfers, {len(result.holders)} holders")

    if not args.skip_verify and args.verify > 0 and result.holders:
        mismatches = verify_against_chain(
            lambda addr: int(
                client.call_contract(args.token, balance_of_calldata(addr), block), 16
            ),
            result.holders,
            args.verify,
        )
        if mismatches:
            _log(
                f"error: {len(mismatches)} of the {min(args.verify, len(result.holders))} largest "
                "balances don't match balanceOf at the snapshot block:"
            )
            for m in mismatches[:5]:
                _log(f"  {m.address}: events say {m.from_events}, contract says {m.on_chain}")
            _log(
                "The token's balances probably change in ways other than Transfer events "
                "(fee-on-transfer, rebasing or reflection), or --from-block is too late. "
                "Use --skip-verify to override."
            )
            return 2

    excluded = set(args.exclude)
    balances = {
        addr: bal
        for addr, bal in result.holders.items()
        if addr not in excluded and bal >= args.min_balance
    }
    if args.top:
        balances = dict(output.ordered(balances)[: args.top])

    stats = summarize(balances)
    if args.stats:
        s = stats.as_dict()
        _log(
            f"holders {s['holders']} | total {s['total']} | top1 {s['top1Share']:.2%} "
            f"| top10 {s['top10Share']:.2%} | gini {s['gini']:.3f}"
        )

    text = (
        output.to_csv(balances)
        if args.format == "csv"
        else output.to_json(balances, token=args.token, block=block, stats=stats.as_dict())
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        _log(f"wrote {len(balances)} holders to {args.output}")
    else:
        sys.stdout.write(text)
    return 0
