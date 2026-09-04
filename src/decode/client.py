"""HTTP client for CoinGlass: retries, timing, and transparent decryption.

Returns a Fetched record rather than bare JSON so the pipeline can archive the
raw bytes alongside the parsed payload -- when a reducer breaks months from now,
the raw blob is what lets you see whether the API changed or our code did.
"""
from __future__ import annotations

import email.utils
import gzip
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import crypto
from .config import BACKOFF, RETRIES, TIMEOUT, USER_AGENT


class FetchError(Exception):
    """Network, HTTP, or API-level failure after retries are exhausted."""


@dataclass
class Fetched:
    url: str
    payload: Any                       # decrypted JSON
    raw: bytes                         # exact response body as received
    status: int
    headers: dict[str, str]
    elapsed_ms: int
    attempts: int
    server_epoch: float | None = None  # from the Date header, for clock checks
    encrypted: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def raw_gz(self) -> bytes:
        """The payload AFTER decryption, gzipped -- this is what gets archived.

        Storing the ciphertext instead would be worthless: the AES key rotates
        per response and lives in the `user` header, so an archived encrypted
        body can never be opened again. The decrypted payload is the earliest
        state we can durably keep, and it is what makes reducer replay possible.
        """
        return gzip.compress(json.dumps(self.payload, separators=(",", ":")).encode(), 6)


def _headers(cache_ts: str, obe: str = "", ua: str = "") -> dict[str, str]:
    h = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "cache-ts-v2": cache_ts,
        "encryption": "true",
        "language": "en",
        "origin": "https://www.coinglass.com",
        "referer": "https://www.coinglass.com/",
        "user-agent": ua or USER_AGENT,
    }
    if obe:                       # optional: verified unnecessary on every stream
        h["obe"] = obe
    return h


def _server_epoch(headers) -> float | None:
    date = headers.get("date")
    if not date:
        return None
    try:
        return email.utils.parsedate_to_datetime(date).timestamp()
    except (TypeError, ValueError):
        return None


def fetch(url: str, *, obe: str = "", ua: str = "", retries: int = RETRIES,
          timeout: int = TIMEOUT) -> Fetched:
    """GET, retry with backoff, decrypt if encrypted, raise FetchError if hopeless."""
    last: Exception | None = None
    started = time.time()
    for attempt in range(1, retries + 1):
        cache_ts = str(int(time.time() * 1000))
        req = urllib.request.Request(url, headers=_headers(cache_ts, obe, ua))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                status = resp.status
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(BACKOFF ** attempt)
            continue

        elapsed = int((time.time() - started) * 1000)
        encrypted = bool(headers.get("user") and headers.get("v"))
        if encrypted:
            payload = crypto.decrypt(body, headers["user"], headers["v"], url,
                                     cache_ts=cache_ts, time_header=headers.get("time", ""))
        else:
            payload = json.loads(body)

        # A 200 carrying {"success": false} is still a failure; retry it, because
        # transient 40001s do happen and a silent bad row is worse than a slow one.
        if isinstance(payload, dict) and payload.get("success") is False:
            last = FetchError(f"api {payload.get('code')}: {payload.get('msg')}")
            if attempt < retries:
                time.sleep(BACKOFF ** attempt)
                continue
            raise last

        return Fetched(url=url, payload=payload, raw=body.encode(), status=status,
                       headers=headers, elapsed_ms=elapsed, attempts=attempt,
                       server_epoch=_server_epoch(headers), encrypted=encrypted)

    raise FetchError(f"{type(last).__name__ if last else 'unknown'}: {last}") from last
