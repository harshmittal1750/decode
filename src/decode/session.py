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
