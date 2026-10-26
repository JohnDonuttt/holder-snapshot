"""A small JSON-RPC client built on the standard library.

Only what the snapshot needs: block number, eth_getLogs and eth_call. Transient failures
(network errors, 429/5xx, provider rate limits) are retried with backoff.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

RATE_LIMIT_CODE = -32005


class RpcError(Exception):
    """The node answered, but with a JSON-RPC error."""

    def __init__(self, code: int, message: str):
        super().__init__(f"rpc error {code}: {message}")
        self.code = code
        self.message = message


class TransportError(Exception):
    """The request never got a usable answer, even after retries."""


class RpcClient:
    def __init__(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 0.5,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._open = opener
        self._sleep = sleep
        self._next_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self._next_id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        ).encode()

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(self.backoff * 2 ** (attempt - 1))
            try:
                body = self._post(payload)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_error = exc
                continue

            if "error" in body and body["error"]:
                err = body["error"]
                if err.get("code") == RATE_LIMIT_CODE:
                    last_error = RpcError(err["code"], err.get("message", ""))
                    continue
                raise RpcError(err.get("code", 0), err.get("message", "unknown error"))
            return body.get("result")

        raise TransportError(f"{method} failed after {self.retries + 1} attempts: {last_error}")

    def _post(self, payload: bytes) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # 429 and 5xx are worth retrying; other 4xx (bad key, bad request) are not
            if exc.code == 429 or exc.code >= 500:
                raise
            raise TransportError(f"http {exc.code} from {self.url}") from exc

    # --- the handful of methods we need -------------------------------------------------

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def get_logs(
        self, address: str, topics: list[Any], from_block: int, to_block: int
    ) -> list[dict]:
        return self.call(
            "eth_getLogs",
            [
                {
                    "address": address,
                    "topics": topics,
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                }
            ],
        )

    def call_contract(self, to: str, data: str, block: int) -> str:
        return self.call("eth_call", [{"to": to, "data": data}, hex(block)])


def fetch_logs_adaptive(
    fetch: Callable[[int, int], list[dict]],
    from_block: int,
    to_block: int,
    chunk: int = 50_000,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Fetch logs over [from_block, to_block], halving the range whenever the node rejects it.

    Providers cap eth_getLogs by block range or result count, and the cap differs per provider,
    so it is discovered at runtime instead of hardcoded.
    """
    logs: list[dict] = []
    total = to_block - from_block + 1
    cursor = from_block

    while cursor <= to_block:
        end = min(cursor + chunk - 1, to_block)
        try:
            logs.extend(fetch(cursor, end))
        except RpcError:
            if chunk <= 1:
                raise
            chunk //= 2
            continue

        cursor = end + 1
        if on_progress:
            on_progress(cursor - from_block, total)
    return logs
