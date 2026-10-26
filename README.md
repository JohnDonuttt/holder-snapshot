# holder-snapshot

Rebuild who holds an ERC-20 token at a given block, using nothing but the token's `Transfer` events. The output is a CSV of `address,amount`, ready for an airdrop, a governance snapshot, or a quick look at how concentrated a token is.

Pure Python standard library: no `web3.py`, no dependencies at all (only `pytest` and `ruff` for development).

```
$ holder-snapshot 0xTokenAddress --rpc $RPC_URL --from-block 17000000 --stats -o holders.csv
snapshot at block 18234567: 48211 transfers, 9120 holders
holders 9120 | total 1000000000000000000000000 | top1 12.40% | top10 41.03% | gini 0.912
wrote 9120 holders to holders.csv
```

## Why replay events?

`balanceOf` only tells you a balance *now*, one address at a time. To get *every* holder at a *past* block you either need an archive node and a list of addresses, or you replay the token's history. Replaying `Transfer` logs gives you all holders at any block with a normal RPC.

The catch: it is only correct for tokens whose balances change exclusively through `Transfer` events. Fee-on-transfer, rebasing and reflection tokens break that. So the tool **checks its own answer**: it compares the largest balances with the contract's real `balanceOf` at the snapshot block, and refuses to write output if they disagree.

## Install and run

Needs Python 3.10+.

```sh
pip install -e .
holder-snapshot --help
# or without installing
python -m holder_snapshot --help
```

```sh
holder-snapshot <token> --rpc <url> [options]
```

| Option | Meaning |
| --- | --- |
| `--rpc URL` | JSON-RPC endpoint (or set `RPC_URL`) |
| `--from-block N` | First block to scan. **Use the token's deployment block.** Default 0 |
| `--block N` | Snapshot block. Default: latest |
| `--min-balance N` | Drop holders below this many base units. Default 1 |
| `--exclude ADDR ...` | Leave out addresses such as liquidity pools, the treasury or a burn address |
| `--top N` | Keep only the N largest holders |
| `--format csv\|json` | CSV is `address,amount`. JSON adds the token, block and stats |
| `-o FILE` | Write to a file instead of stdout |
| `--stats` | Print holder count, top-1 and top-10 share and Gini coefficient to stderr |
| `--verify N` | Check the N largest balances against `balanceOf` at the snapshot block. Default 10 |
| `--skip-verify` | Don't check against `balanceOf` |
| `--chunk N` | Starting block range per request. It shrinks automatically if the node rejects it |

Data goes to stdout and everything else to stderr, so you can pipe the CSV onwards.

**Exit codes:** `0` ok, `1` error (bad input, RPC failure, incomplete history), `2` verification failed.

## Pairing with merkle-airdrop

The CSV is exactly what [`merkle-airdrop`](https://github.com/JohnDonuttt/merkle-airdrop) reads, so a snapshot becomes a claimable airdrop in two commands:

```sh
holder-snapshot $TOKEN --rpc $RPC_URL --from-block $DEPLOY_BLOCK \
  --exclude $UNISWAP_POOL $TREASURY -o holders.csv
merkle-airdrop build holders.csv -o tree.json
```

Amounts are written as full integers (never scientific notation) and addresses as lowercase, which `merkle-airdrop` accepts.

## What it guards against

- **Missing history.** If an address spends more than it holds, the log history is incomplete (usually `--from-block` is after deployment). That is an error naming the address and block, not a silently wrong snapshot.
- **ERC-721 collisions.** NFTs emit a `Transfer` with the same signature but an indexed token id. Logs with four topics are ignored.
- **Ordering.** Logs are applied by block number, then log index, regardless of the order the node returned them. Within a block, order decides whether a spend is valid.
- **Provider limits.** `eth_getLogs` ranges the node rejects are halved and retried. Network errors, HTTP 429/5xx and JSON-RPC rate-limit errors (`-32005`) are retried with exponential backoff. Other errors are raised immediately.
- **Big numbers.** Python integers are arbitrary precision, so 18-decimal and larger token amounts never lose precision.

## Testing

```sh
pip install -e ".[dev]"
pytest
```

61 tests, in two layers:

- **Unit tests (57)** use a fake node: event decoding, replay (mint, burn, self-transfer, ordering within a block, incomplete history), Gini and top-share maths, CSV and JSON output, the retry and backoff behaviour of the RPC client, adaptive range splitting, and the CLI end to end, including every exit code.
- **End-to-end tests (4)** start a real `anvil` chain, deploy a token, mint, transfer and burn, and run the CLI against it. One test deploys a deliberately fee-on-transfer token and confirms verification catches it. They skip themselves when Foundry isn't installed.

## Limitations

- Needs the right `--from-block`. There is no automatic deployment-block lookup, since that requires an archive node.
- Only ERC-20 tokens whose balances move through `Transfer`. Rebasing and reflection tokens are detected, not supported.
- It holds all logs in memory. That is fine for most tokens, but a token with tens of millions of transfers would want streaming.
- Verification samples the largest holders, so it is strong evidence rather than proof for the long tail.
- Output addresses are lowercase, not EIP-55 checksummed (standard-library Python has no Keccak).

## License

MIT
