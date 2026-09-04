"""The token port must reproduce the browser's own output exactly.

These three tokens were captured from real CoinGlass requests. If CoinGlass
rotates the bundle key, these fail loudly -- which is the signal to re-derive
token.py from the new bundle, rather than watching every heatmap fetch 40001.
"""
import time

import pytest

from decode import token
from decode.token import ClockSkewError

CAPTURED = [
    ("vCow9HOM49mNT8X/h975mo7BTD1DjkFJZMHM7GG1pkU=", 1787730160, "202443"),
    ("uDcLJ6ojryn17ONJpskxvdASIaFI9BoTFTmpMyb3cok=", 1787668846, "189607"),
    ("JM/jLHOWoB4ZYA2uTdNZbvLlPpoT3luXJlsuJxJJIYg=", 1787606154, "672054"),
]


@pytest.mark.parametrize("blob,ts,code", CAPTURED)
def test_decodes_captured_token(blob, ts, code):
    assert token.read_token(blob) == (ts, code)


@pytest.mark.parametrize("blob,ts,code", CAPTURED)
def test_totp_matches_browser(blob, ts, code):
    assert token.totp(t=ts) == code


@pytest.mark.parametrize("blob,ts,code", CAPTURED)
def test_round_trip_is_byte_exact(blob, ts, code):
    assert token.make_token(t=ts) == blob


def test_fresh_token_decodes_to_now():
    ts, code = token.read_token(token.make_token())
    assert abs(ts - time.time()) < 5
    assert token.totp(t=ts) == code


def test_totp_rotates_every_30s():
    base = 1787730160
    assert token.totp(t=base) == token.totp(t=base + 29 - base % 30)
    assert token.totp(t=base) != token.totp(t=base + 60)


def test_clock_skew_rejected():
    """A drifted box must fail loudly, not silently 40001 on every heatmap."""
    with pytest.raises(ClockSkewError):
        token.check_clock(time.time() - 300)
    token.check_clock(time.time())      # in sync: no raise
    token.check_clock(None)             # no Date header: cannot check, no raise
