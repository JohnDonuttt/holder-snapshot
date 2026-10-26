import pytest
from helpers import addr, mint, topic, transfer_log

from holder_snapshot.balances import (
    TRANSFER_TOPIC,
    ZERO_ADDRESS,
    InconsistentHistory,
    balance_of_calldata,
    decode_transfer,
    replay,
    verify_against_chain,
)

A, B, C = addr(0xA), addr(0xB), addr(0xC)


def test_decodes_an_erc20_transfer():
    t = decode_transfer(transfer_log(A, B, 12345, block=7, index=3))
    assert (t.sender, t.recipient, t.amount, t.block, t.log_index) == (A, B, 12345, 7, 3)


def test_decodes_a_max_uint256_amount():
    t = decode_transfer(transfer_log(A, B, 2**256 - 1, block=1))
    assert t.amount == 2**256 - 1


def test_ignores_erc721_transfers_which_have_four_topics():
    log = transfer_log(A, B, 0, block=1)
    log["topics"].append("0x" + "00" * 31 + "07")  # tokenId
    log["data"] = "0x"
    assert decode_transfer(log) is None


def test_a_fourth_topic_alone_is_enough_to_reject_a_log():
    # unusual, but a 4-topic Transfer is never an ERC-20 transfer even if it also carries data
    log = transfer_log(A, B, 99, block=1)
    log["topics"].append("0x" + "00" * 31 + "07")
    assert log["data"] != "0x"
    assert decode_transfer(log) is None


def test_ignores_other_events_and_malformed_logs():
    other = transfer_log(A, B, 1, block=1)
    other["topics"][0] = "0x" + "11" * 32
    assert decode_transfer(other) is None

    no_data = transfer_log(A, B, 1, block=1)
    no_data["data"] = "0x"
    assert decode_transfer(no_data) is None


def test_addresses_are_normalised_to_lowercase():
    log = transfer_log(A, B, 1, block=1)
    log["topics"][1] = "0x" + "00" * 12 + "AB" * 20
    assert decode_transfer(log).sender == "0x" + "ab" * 20


def test_replay_applies_mints_transfers_and_burns():
    logs = [
        mint(A, 1000, block=1),
        transfer_log(A, B, 300, block=2),
        transfer_log(B, C, 100, block=3),
        transfer_log(A, ZERO_ADDRESS, 200, block=4),  # burn
    ]
    result = replay(logs)

    assert result.holders == {A: 500, B: 200, C: 100}
    assert result.minted == 1000
    assert result.burned == 200
    assert result.supply == 800 == sum(result.holders.values())
    assert result.transfers == 4


def test_replay_uses_chain_order_not_input_order():
    # B receives and spends within the same block, so log index decides whether that's valid
    logs = [
        transfer_log(B, C, 50, block=5, index=2),
        transfer_log(A, B, 50, block=5, index=1),
        mint(A, 100, block=1),
    ]
    assert replay(logs).holders == {A: 50, C: 50}


def test_replay_drops_zero_balances():
    logs = [mint(A, 10, block=1), transfer_log(A, B, 10, block=2)]
    assert replay(logs).holders == {B: 10}


def test_replay_ignores_non_erc20_logs():
    nft = transfer_log(A, B, 0, block=2)
    nft["topics"].append(topic(addr(1)))
    assert replay([mint(A, 5, block=1), nft]).holders == {A: 5}


def test_overspending_means_the_history_is_incomplete():
    # as if the scan started after A received its tokens
    with pytest.raises(InconsistentHistory) as exc:
        replay([transfer_log(A, B, 10, block=50)])
    assert "deployed" in str(exc.value)


def test_self_transfer_changes_nothing():
    assert replay([mint(A, 10, block=1), transfer_log(A, A, 10, block=2)]).holders == {A: 10}


def test_zero_address_never_appears_as_a_holder():
    assert (
        ZERO_ADDRESS
        not in replay([mint(A, 10, block=1), transfer_log(A, ZERO_ADDRESS, 4, block=2)]).holders
    )


def test_verify_reports_only_the_mismatches():
    balances = {A: 100, B: 50, C: 10}
    chain = {A: 100, B: 49, C: 10}

    mismatches = verify_against_chain(lambda a: chain[a], balances, sample=10)

    assert [(m.address, m.from_events, m.on_chain) for m in mismatches] == [(B, 50, 49)]


def test_verify_only_checks_the_requested_number_of_largest_holders():
    balances = {A: 100, B: 50, C: 10}
    checked = []

    def balance_of(a):
        checked.append(a)
        return balances[a]

    verify_against_chain(balance_of, balances, sample=2)
    assert checked == [A, B]


def test_calldata_pads_the_address():
    data = balance_of_calldata(A)
    assert data.startswith("0x70a08231")
    assert data.endswith(A[2:])
    assert len(data) == 2 + 8 + 64


def test_topic_constant_is_the_transfer_signature():
    assert TRANSFER_TOPIC == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
