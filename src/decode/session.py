"""The persisted `obe` session value. The only module that touches session.json.

Written by `decode login` (browser capture), read by `decode collect` as the
default `--obe` when none is passed explicitly.
"""
from __future__ import annotations

import json
import time

from .config import SESSION_PATH


def read_obe() -> str:
    """The last captured obe value, or "" if none was ever captured."""
    try:
        return json.loads(SESSION_PATH.read_text()).get("obe", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def write_obe(value: str) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps({"obe": value, "captured_at": int(time.time() * 1000)}))


# The obe cookie is issued with maxAge 15552e3 (180 days) and is NOT refreshed
# by use -- API responses carry no Set-Cookie, verified. So the clock runs from
# login regardless of activity, and renewal needs a real browser plus a human.
SESSION_TTL_DAYS = 180
WARN_WITHIN_DAYS = 21


def age_days() -> float | None:
    """Days since the session was captured, or None if we never stored one."""
    import json
    from .config import SESSION_PATH
    try:
        captured = json.loads(SESSION_PATH.read_text()).get("captured_at")
    except (OSError, ValueError):
        return None
    return None if not captured else (time.time() * 1000 - captured) / 86_400_000


def expiry_warning() -> str | None:
    """A heads-up while there is still time to act, not after it breaks.

    Renewal is interactive (visible browser + a person), so on a headless host
    it means: run `decode login` on a machine with a display and copy
    data/session.json across. That is worth knowing weeks ahead, not on the
    morning five heatmap streams start failing.
    """
    age = age_days()
    if age is None:
        return None
    left = SESSION_TTL_DAYS - age
    if left <= 0:
        return ("session is past its 180-day life -- the 5 session-gated heatmap "
                "windows will fail; run `decode login` (needs a display)")
    if left <= WARN_WITHIN_DAYS:
        return (f"session expires in ~{left:.0f} days; renew with `decode login` on a "
                "machine with a display, then copy data/session.json to this host")
    return None
