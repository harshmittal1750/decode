"""The `data=` query token required by the liqHeatMap endpoints.

Ported verbatim from the site's _app bundle, webpack module 94126:

    function a(){
      var t = parseInt((new Date).getTime()/1e3);
      var e = "".concat(t, ",", i.authenticator.generate("I65VU7K5ZQL7WB4E",
                                                         {time:t, step:30}));
      return o.AES.encrypt(e, o.enc.Utf8.parse("1f68efd73f8d4921acc0dead41dd39bc"),
                           {mode:o.mode.ECB, padding:o.pad.Pkcs7}).toString()
    }

plaintext "<unix_seconds>,<6-digit TOTP>", AES-256-ECB (the 32-char key is used
as raw UTF-8 bytes, not hex-decoded), PKCS7, base64.

Both constants ship in plaintext to every visitor, so this is obfuscation and
not a secret. It gates nothing that opening the page would not give you. It can
be rotated in any CoinGlass deploy -- the round-trip test is what tells you.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .config import MAX_CLOCK_SKEW_SEC

OBF_KEY = b"1f68efd73f8d4921acc0dead41dd39bc"   # 32 ASCII chars -> AES-256
TOTP_SEED = "I65VU7K5ZQL7WB4E"
STEP = 30


class ClockSkewError(Exception):
    """Local clock too far from the server's for the TOTP to be accepted."""


def totp(seed: str = TOTP_SEED, t: float | None = None, step: int = STEP, digits: int = 6) -> str:
    """RFC 6238 TOTP, HMAC-SHA1 -- matching otplib's authenticator defaults."""
    key = base64.b32decode(seed + "=" * (-len(seed) % 8))
    counter = int((time.time() if t is None else t) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10 ** digits).zfill(digits)


def _cipher() -> AES:
    return AES.new(OBF_KEY, AES.MODE_ECB)


def make_token(t: float | None = None) -> str:
    """Mint a fresh token. Pass `t` only to reproduce a known one in tests."""
    t = int(time.time() if t is None else t)
    plain = f"{t},{totp(t=t)}".encode()
    return base64.b64encode(_cipher().encrypt(pad(plain, AES.block_size))).decode()


def read_token(token: str) -> tuple[int, str]:
    """(unix_seconds, totp) from an existing token. Used to verify the port."""
    plain = unpad(_cipher().decrypt(base64.b64decode(token)), AES.block_size).decode()
    ts, code = plain.split(",")
    return int(ts), code


def check_clock(server_epoch: float | None) -> None:
    """Refuse to mint tokens on a badly drifted clock.

    The TOTP step is 30s, so a box whose clock is minutes off produces codes the
    server rejects -- and the only symptom is every heatmap fetch returning
    40001. Failing loudly here turns a silent data gap into an obvious error.
    """
    if server_epoch is None:
        return
    skew = abs(time.time() - server_epoch)
    if skew > MAX_CLOCK_SKEW_SEC:
        raise ClockSkewError(
            f"local clock is {skew:.0f}s from server (max {MAX_CLOCK_SKEW_SEC}s); "
            "sync NTP or heatmap requests will be rejected"
        )
