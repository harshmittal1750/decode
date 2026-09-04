"""CoinGlass response decryption.

Reverse-engineered from the site's own webpack module 12471:

    key0       = base64(<constant per `v` header>)[:16]
    actual_key = gunzip(AES-128-ECB(base64(`user` header), key0))
    plaintext  = gunzip(AES-128-ECB(base64(body["data"]), actual_key))

The key rotates per response, so a saved response body alone can never be
decrypted -- the `user` header must be captured with it.
"""
from __future__ import annotations

import base64
import gzip
import json
from typing import Any
from urllib.parse import urlparse

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


class DecryptError(Exception):
    """Raised when a response cannot be decrypted (bad key, bad `v`, garbage)."""


# `v` selects which constant seeds key0. 0/1/2 derive from request context;
# 55/66/77 are baked-in constants. All are live -- despite older write-ups
# describing 55/66/77 as deprecated, they are what the API returns in practice.
_CONSTANTS = {
    "55": "170b070da9654622",
    "66": "d6537d845a964081",
    "77": "863f08689c97435b",
}


def derive_key0(v: str, url: str = "", *, cache_ts: str = "", time_header: str = "") -> str:
    if v == "0":
        const = cache_ts
    elif v == "1":
        const = urlparse(url).path or url.split("?")[0]
    elif v == "2":
        const = time_header
    else:
        const = _CONSTANTS.get(v, "")
    if not const:
        raise DecryptError(f"cannot derive key0 for v={v!r} (missing context)")
    return base64.b64encode(const.encode()).decode()[:16]


def _ecb_gunzip(blob: bytes, key: str) -> bytes:
    try:
        plain = unpad(AES.new(key.encode(), AES.MODE_ECB).decrypt(blob), AES.block_size)
    except ValueError as exc:
        raise DecryptError(f"AES unpad failed (wrong key?): {exc}") from exc
    try:
        return gzip.decompress(plain)
    except OSError as exc:
        raise DecryptError(f"gunzip failed after decrypt: {exc}") from exc


def decrypt(body: str, user_header: str, v: str, url: str = "",
            *, cache_ts: str = "", time_header: str = "") -> Any:
    """Decrypt one CoinGlass response body into its JSON payload."""
    try:
        outer = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DecryptError(f"response is not JSON: {exc}") from exc
    if "data" not in outer:
        raise DecryptError(f"no data field; keys={list(outer)}")

    key0 = derive_key0(v, url, cache_ts=cache_ts, time_header=time_header)
    actual_key = _ecb_gunzip(base64.b64decode(user_header), key0).decode()
    return json.loads(_ecb_gunzip(base64.b64decode(outer["data"]), actual_key))
