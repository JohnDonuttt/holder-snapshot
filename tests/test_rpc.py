import io
import json
import urllib.error

import pytest

from holder_snapshot.rpc import RpcClient, RpcError, TransportError, fetch_logs_adaptive


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok(result):
    return FakeResponse(json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode())


def rpc_error(code, message="boom"):
    return FakeResponse(
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": message}}
        ).encode()
    )


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


def scripted(*steps):
    """An opener that plays each step in order: an exception is raised, a response is returned."""
    calls = []
    queue = list(steps)

    def opener(request, timeout):
        calls.append(json.loads(request.data))
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    opener.calls = calls
    return opener


def client(opener):
    sleeps = []
    c = RpcClient("http://x", opener=opener, sleep=sleeps.append, backoff=1.0)
    c.sleeps = sleeps
    return c


def test_returns_the_result():
    c = client(scripted(ok("0x10")))
    assert c.block_number() == 16


def test_request_is_well_formed_json_rpc():
    opener = scripted(ok("0x1"))
    client(opener).call("eth_chainId", [])
    assert opener.calls[0] == {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}


def test_retries_network_errors_with_exponential_backoff():
    c = client(scripted(urllib.error.URLError("down"), urllib.error.URLError("down"), ok("0x2")))
    assert c.call("m", []) == "0x2"
    assert c.sleeps == [1.0, 2.0]


def test_retries_429_and_5xx_but_not_other_client_errors():
    assert client(scripted(http_error(429), ok("0x1"))).call("m", []) == "0x1"
    assert client(scripted(http_error(503), ok("0x1"))).call("m", []) == "0x1"

    with pytest.raises(TransportError, match="401"):
        client(scripted(http_error(401))).call("m", [])


def test_rate_limit_errors_inside_a_200_are_retried():
    c = client(scripted(rpc_error(-32005, "limit exceeded"), ok("0x9")))
    assert c.call("m", []) == "0x9"


def test_other_rpc_errors_are_raised_immediately_without_retry():
    opener = scripted(rpc_error(3, "execution reverted"))
    with pytest.raises(RpcError) as exc:
        client(opener).call("eth_call", [])
    assert exc.value.code == 3
    assert len(opener.calls) == 1


def test_gives_up_after_the_configured_retries():
    opener = scripted(*[urllib.error.URLError("down")] * 4)
    with pytest.raises(TransportError, match="4 attempts"):
        client(opener).call("m", [])
    assert len(opener.calls) == 4


def test_get_logs_sends_hex_block_bounds_and_topics():
    opener = scripted(ok([]))
    client(opener).get_logs("0xabc", ["0xt"], 16, 255)
    params = opener.calls[0]["params"][0]
    assert params == {"address": "0xabc", "topics": ["0xt"], "fromBlock": "0x10", "toBlock": "0xff"}


# --- fetch_logs_adaptive ---------------------------------------------------------------


def test_adaptive_covers_the_range_once_in_chunks():
    seen = []

    def fetch(lo, hi):
        seen.append((lo, hi))
        return [{"range": (lo, hi)}]

    logs = fetch_logs_adaptive(fetch, 0, 24, chunk=10)
    assert seen == [(0, 9), (10, 19), (20, 24)]
    assert len(logs) == 3


def test_adaptive_halves_the_chunk_when_the_node_rejects_a_range():
    def fetch(lo, hi):
        if hi - lo + 1 > 25:
            raise RpcError(-32000, "too many results")
        return [lo]

    assert fetch_logs_adaptive(fetch, 0, 99, chunk=100) == [0, 25, 50, 75]


def test_adaptive_raises_once_a_single_block_still_fails():
    def fetch(lo, hi):
        raise RpcError(-32000, "nope")

    with pytest.raises(RpcError):
        fetch_logs_adaptive(fetch, 0, 3, chunk=4)


def test_adaptive_reports_progress():
    done = []
    fetch_logs_adaptive(
        lambda lo, hi: [], 0, 19, chunk=10, on_progress=lambda d, t: done.append((d, t))
    )
    assert done == [(10, 20), (20, 20)]


def test_adaptive_single_block_range():
    assert fetch_logs_adaptive(lambda lo, hi: [(lo, hi)], 5, 5) == [(5, 5)]
