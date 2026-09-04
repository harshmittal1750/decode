#!/usr/bin/env python3
"""Decrypt CoinGlass API responses. Stdlib + openssl only, no pip installs.

Scheme (webpack module 12471):
  key0       = base64(url_path)[:16]                  # v=1
  actual_key = gunzip(AES-128-ECB(user_header, key0))
  plaintext  = gunzip(AES-128-ECB(data, actual_key))
"""
import base64, gzip, json, subprocess, sys, time, urllib.request
from urllib.parse import urlparse

def aes_ecb_dec(blob: bytes, key: str) -> bytes:
    # ponytail: shells out to openssl; swap for pycryptodome if you ever pip install
    return subprocess.run(
        ["openssl", "enc", "-d", "-aes-128-ecb", "-K", key.encode().hex()],
        input=blob, capture_output=True, check=True).stdout

def key0_for(v: str, url: str, cache_ts: str = "", time_hdr: str = "") -> str:
    const = {"1": urlparse(url).path, "0": cache_ts, "2": time_hdr,
             "55": "170b070da9654622", "66": "d6537d845a964081",
             "77": "863f08689c97435b"}[v]
    if not const:
        raise ValueError(f"v={v} needs its extra header")
    return base64.b64encode(const.encode()).decode()[:16]

def decrypt(body: str, user_hdr: str, v: str, url="", cache_ts="", time_hdr=""):
    key = gzip.decompress(aes_ecb_dec(base64.b64decode(user_hdr),
                                      key0_for(v, url, cache_ts, time_hdr))).decode()
    payload = base64.b64decode(json.loads(body)["data"])
    return json.loads(gzip.decompress(aes_ecb_dec(payload, key)))

def fetch(url: str):
    cache_ts = str(int(time.time() * 1000))
    req = urllib.request.Request(url, headers={
        "accept": "application/json, text/plain, */*",
        "cache-ts-v2": cache_ts, "encryption": "true", "language": "en",
        "origin": "https://www.coinglass.com", "referer": "https://www.coinglass.com/",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        body, h = r.read().decode(), r.headers
    if not h.get("user"):
        return json.loads(body)
    return decrypt(body, h["user"], h["v"], url, cache_ts, h.get("time", ""))

if __name__ == "__main__":
    print(json.dumps(fetch(sys.argv[1]), indent=2)[:3000])


def fetch_curl(url: str, obe: str = "", ua: str = ""):
    """Same as fetch(), plus the `obe` session header some endpoints require."""
    import urllib.request
    cache_ts = str(int(time.time() * 1000))
    h = {"accept": "application/json", "accept-language": "en-US,en;q=0.9",
         "cache-ts-v2": cache_ts, "encryption": "true", "language": "en",
         "origin": "https://www.coinglass.com", "referer": "https://www.coinglass.com/",
         "user-agent": ua or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"}
    if obe:
        h["obe"] = obe
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        body, rh = r.read().decode(), r.headers
    if not rh.get("user"):
        return json.loads(body)
    return decrypt(body, rh["user"], rh["v"], url, cache_ts, rh.get("time", ""))
