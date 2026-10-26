"""End to end against a real local chain. Skipped when Foundry isn't installed."""

from __future__ import annotations

import csv
import io
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from holder_snapshot.cli import main
from holder_snapshot.rpc import RpcClient

FIXTURES = Path(__file__).parent / "fixtures"
# anvil's well-known development account #0; this key is public and holds only fake ETH
DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEV = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
ALICE = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"
BOB = "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"
CAROL = "0x90f79bf6eb2c4f870365e785982e1f101e93b906"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("anvil", "forge", "cast")),
    reason="needs Foundry (anvil, forge, cast)",
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(["anvil", "--silent", "--port", str(port)])

    client = RpcClient(url, retries=0)
    for _ in range(50):
        try:
            client.block_number()
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("anvil did not start")

    work = tmp_path_factory.mktemp("forge")
    (work / "src").mkdir()
    (work / "src" / "Tokens.sol").write_text((FIXTURES / "Tokens.sol").read_text())

    class Chain:
        pass

    c = Chain()
    c.url, c.work = url, work

    def deploy(name: str) -> str:
        out = subprocess.run(
            ["forge", "create", f"src/Tokens.sol:{name}", "--root", str(work), "--rpc-url", url,
             "--private-key", DEV_KEY, "--broadcast", "--json"],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        return json.loads(out.stdout)["deployedTo"].lower()

    def send(token: str, signature: str, *args: str, key: str = DEV_KEY) -> None:
        subprocess.run(
            ["cast", "send", token, signature, *args, "--rpc-url", url, "--private-key", key],
            capture_output=True, check=True,
        )  # fmt: skip

    c.deploy, c.send = deploy, send
    yield c
    proc.terminate()
    proc.wait(timeout=10)


def run_cli(capsys, chain, token, *args):
    code = main([token, "--rpc", chain.url, *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_snapshot_of_a_real_token_matches_what_the_chain_says(chain, capsys):
    token = chain.deploy("MockToken")
    chain.send(token, "mint(address,uint256)", ALICE, "1000")
    chain.send(token, "mint(address,uint256)", BOB, "500")
    chain.send(token, "mint(address,uint256)", DEV, "10000000000000000000000000000000000")  # 1e34
    chain.send(token, "transfer(address,uint256)", CAROL, "250", key=DEV_KEY)
    chain.send(token, "burn(uint256)", "100", key=DEV_KEY)

    code, out, err = run_cli(capsys, chain, token, "--stats")

    assert code == 0, err
    balances = {r["address"]: int(r["amount"]) for r in csv.DictReader(io.StringIO(out))}
    assert balances == {
        DEV: 10**34 - 250 - 100,
        CAROL: 250,
        ALICE: 1000,
        BOB: 500,
    }
    # independently confirm one of the numbers against the contract itself
    on_chain = subprocess.run(
        ["cast", "call", token, "balanceOf(address)(uint256)", ALICE, "--rpc-url", chain.url],
        capture_output=True, text=True, check=True,
    ).stdout.split()[0]  # fmt: skip
    assert int(on_chain) == balances[ALICE]
    assert "holders 4" in err


def test_snapshot_at_an_earlier_block_ignores_later_transfers(chain, capsys):
    token = chain.deploy("MockToken")
    chain.send(token, "mint(address,uint256)", ALICE, "100")
    early = RpcClient(chain.url).block_number()
    chain.send(token, "mint(address,uint256)", BOB, "200")
    chain.send(token, "transfer(address,uint256)", BOB, "0", key=DEV_KEY)

    code, out, _ = run_cli(capsys, chain, token, "--block", str(early))

    assert code == 0
    assert {r["address"] for r in csv.DictReader(io.StringIO(out))} == {ALICE}


def test_a_fee_on_transfer_token_is_caught_by_verification(chain, capsys):
    token = chain.deploy("FeeToken")
    chain.send(token, "mint(address,uint256)", DEV, "1000")
    chain.send(token, "transfer(address,uint256)", ALICE, "100", key=DEV_KEY)

    code, out, err = run_cli(capsys, chain, token)

    assert code == 2
    assert out == ""
    assert "don't match balanceOf" in err

    # with verification off the replay still runs, just with the wrong numbers
    code, out, _ = run_cli(capsys, chain, token, "--skip-verify")
    assert code == 0
    assert {r["address"]: int(r["amount"]) for r in csv.DictReader(io.StringIO(out))}[ALICE] == 100


def test_starting_after_deployment_is_reported_clearly(chain, capsys):
    token = chain.deploy("MockToken")
    chain.send(token, "mint(address,uint256)", DEV, "100")
    chain.send(token, "transfer(address,uint256)", ALICE, "40", key=DEV_KEY)
    head = RpcClient(chain.url).block_number()

    code, _, err = run_cli(capsys, chain, token, "--from-block", str(head), "--skip-verify")

    assert code == 1
    assert "deployed" in err
