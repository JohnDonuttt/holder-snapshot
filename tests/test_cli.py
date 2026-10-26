import csv
import io
import json

import pytest
from helpers import FakeClient, addr, mint, transfer_log

from holder_snapshot.balances import ZERO_ADDRESS
from holder_snapshot.cli import main

TOKEN = addr(0x70)
A, B, C, D = addr(0xA), addr(0xB), addr(0xC), addr(0xD)


def history():
    return [
        mint(A, 1000, block=1),
        transfer_log(A, B, 300, block=2),
        transfer_log(B, C, 100, block=3),
        transfer_log(A, ZERO_ADDRESS, 100, block=4),
        mint(D, 5, block=5),
    ]


def run(capsys, client, *args):
    code = main([TOKEN, *args], client=client)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_prints_csv_largest_first(capsys):
    code, out, err = run(capsys, FakeClient(history()))

    assert code == 0
    assert [(r["address"], r["amount"]) for r in rows(out)] == [
        (A, "600"),
        (B, "200"),
        (C, "100"),
        (D, "5"),
    ]
    assert "snapshot at block 100" in err


def test_snapshot_block_limits_the_history(capsys):
    code, out, _ = run(capsys, FakeClient(history()), "--block", "2")

    assert code == 0
    assert [(r["address"], r["amount"]) for r in rows(out)] == [(A, "700"), (B, "300")]


def test_min_balance_exclude_and_top(capsys):
    client = FakeClient(history())

    _, out, _ = run(capsys, client, "--min-balance", "100")
    assert [r["address"] for r in rows(out)] == [A, B, C]

    _, out, _ = run(capsys, client, "--exclude", A.upper().replace("0X", "0x"))
    assert [r["address"] for r in rows(out)] == [B, C, D]

    _, out, _ = run(capsys, client, "--top", "2")
    assert [r["address"] for r in rows(out)] == [A, B]


def test_json_format_includes_block_and_stats(capsys):
    _, out, _ = run(capsys, FakeClient(history()), "--format", "json")
    doc = json.loads(out)

    assert doc["token"] == TOKEN and doc["block"] == 100
    assert doc["holders"][0] == {"address": A, "amount": "600"}
    assert doc["stats"]["holders"] == 4


def test_stats_flag_prints_a_summary_to_stderr(capsys):
    _, out, err = run(capsys, FakeClient(history()), "--stats")
    assert "holders 4" in err and "gini" in err
    assert "gini" not in out  # stdout stays pure data


def test_writes_to_a_file(capsys, tmp_path):
    target = tmp_path / "holders.csv"
    code, out, err = run(capsys, FakeClient(history()), "-o", str(target))

    assert code == 0 and out == ""
    assert rows(target.read_text())[0]["address"] == A
    assert "wrote 4 holders" in err


def test_works_when_the_provider_rejects_wide_ranges(capsys):
    client = FakeClient(history(), head=100)
    client.max_range = 20

    code, out, _ = run(capsys, client, "--chunk", "100")

    assert code == 0
    assert len(rows(out)) == 4
    # the first wide request was rejected, so it must have retried with narrower ranges
    assert len(client.log_requests) > 1
    assert client.log_requests[-1][1] - client.log_requests[-1][0] + 1 <= 20


def test_verification_failure_exits_2_and_writes_nothing(capsys):
    # the contract says A holds less than the events imply, like a fee-on-transfer token
    client = FakeClient(history(), balance_overrides={A: 590})

    code, out, err = run(capsys, client)

    assert code == 2
    assert out == ""
    assert "don't match balanceOf" in err and "events say 600, contract says 590" in err


def test_skip_verify_lets_a_mismatching_token_through(capsys):
    client = FakeClient(history(), balance_overrides={A: 590})
    code, out, _ = run(capsys, client, "--skip-verify")
    assert code == 0 and rows(out)


def test_verify_only_looks_at_the_largest_holders(capsys):
    # D is the smallest holder, outside the top 2, so its bad balance isn't noticed
    client = FakeClient(history(), balance_overrides={D: 999})
    code, _, _ = run(capsys, client, "--verify", "2")
    assert code == 0


def test_incomplete_history_is_a_clear_error(capsys):
    client = FakeClient([transfer_log(A, B, 10, block=50)])
    code, out, err = run(capsys, client, "--skip-verify")

    assert code == 1 and out == ""
    assert "deployed" in err


def test_from_block_after_the_snapshot_block_is_rejected(capsys):
    code, _, err = run(capsys, FakeClient(history()), "--from-block", "50", "--block", "10")
    assert code == 1 and "after the snapshot block" in err


def test_missing_rpc_is_a_clear_error(capsys, monkeypatch):
    monkeypatch.delenv("RPC_URL", raising=False)
    code = main([TOKEN])
    assert code == 1
    assert "no RPC endpoint" in capsys.readouterr().err


def test_bad_token_address_is_rejected_by_argparse():
    with pytest.raises(SystemExit) as exc:
        main(["not-an-address"], client=FakeClient([]))
    assert exc.value.code == 2


def test_empty_token_history_gives_an_empty_csv(capsys):
    code, out, _ = run(capsys, FakeClient([]))
    assert code == 0
    assert out == "address,amount\n"
