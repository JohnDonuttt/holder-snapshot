import json

from holder_snapshot.output import ordered, to_csv, to_json


def test_largest_first_with_address_tiebreak():
    assert ordered({"0xb": 5, "0xa": 5, "0xc": 9}) == [("0xc", 9), ("0xa", 5), ("0xb", 5)]


def test_csv_has_the_header_merkle_airdrop_expects():
    text = to_csv({"0xa": 10**30, "0xb": 7})
    lines = text.splitlines()
    assert lines[0] == "address,amount"
    assert lines[1] == f"0xa,{10**30}"  # full integer, never scientific notation
    assert lines[2] == "0xb,7"


def test_empty_csv_is_just_the_header():
    assert to_csv({}) == "address,amount\n"


def test_json_keeps_amounts_as_strings():
    doc = json.loads(to_json({"0xa": 2**200}, token="0xt", block=9, stats={"holders": 1}))
    assert doc["token"] == "0xt" and doc["block"] == 9
    assert doc["holders"] == [{"address": "0xa", "amount": str(2**200)}]
    assert doc["stats"] == {"holders": 1}
