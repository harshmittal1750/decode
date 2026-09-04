"""Paths, retention policy and tunables. No logic here on purpose."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("DECODE_ROOT", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
DB_PATH = Path(os.environ.get("DECODE_DB", DATA / "decode.db"))
LOG_PATH = DATA / "decode.log"
SESSION_PATH = DATA / "session.json"

API = "https://capi.coinglass.com/api"

# --- network -----------------------------------------------------------------
TIMEOUT = 30
RETRIES = 3                     # total attempts per stream
BACKOFF = 1.7                   # seconds, exponential: 1.7, 2.9, 4.9 ...
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# --- retention ---------------------------------------------------------------
# The rule that matters: retention follows REPLAYABILITY, not a disk budget.
#
# funding / basis / longshort / liqtoday return their own history on every call,
# so their raw payloads are only worth keeping as a debugging window.
#
# The liq heatmap is NOT replayable -- swept levels decay out of the payload and
# are archived nowhere. Its *processed* live book is therefore kept forever, and
# no retention sweep may ever touch the processed table.
RAW_RETENTION_DAYS = 14         # applies to raw blobs only, never to processed
PROCESSED_RETENTION_DAYS = None  # never expire; the archive is the whole point

# Local clock feeds the TOTP that authorises heatmap requests. A drifting box
# (a Pi without NTP) silently fails every heatmap fetch, so we refuse to mint a
# token when we are further than this from the server's own clock.
MAX_CLOCK_SKEW_SEC = 25
