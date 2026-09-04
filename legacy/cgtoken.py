#!/usr/bin/env python3
"""Generate the `data=` query token CoinGlass requires on liqHeatMap endpoints.

Ported from the site's own _app bundle (module 94126), which ships this
verbatim to every browser -- it is client-side obfuscation, not a secret:

    function a(){
      var t = parseInt((new Date).getTime()/1e3);
      var e = "".concat(t, ",", i.authenticator.generate("I65VU7K5ZQL7WB4E",
                                                         {time:t, step:30}));
      return o.AES.encrypt(e, o.enc.Utf8.parse("1f68efd73f8d4921acc0dead41dd39bc"),
                           {mode:o.mode.ECB, padding:o.pad.Pkcs7}).toString()
    }

So: plaintext "<unix_seconds>,<6-digit TOTP>", AES-256-ECB (the 32-char key is
used as raw UTF-8 bytes, not hex-decoded), PKCS7, base64.

The TOTP step is 30s, which is why a captured token dies quickly -- generate a
fresh one per request instead of pasting one from devtools.
"""
import base64, hashlib, hmac, struct, subprocess, time

OBF_KEY = "1f68efd73f8d4921acc0dead41dd39bc"   # 32 ASCII chars -> AES-256
TOTP_SEED = "I65VU7K5ZQL7WB4E"                  # base32, from the same bundle
STEP = 30


def totp(seed=TOTP_SEED, t=None, step=STEP, digits=6):
    """RFC 6238 TOTP, HMAC-SHA1 -- otplib's authenticator defaults."""
    key = base64.b32decode(seed + "=" * (-len(seed) % 8))
    ctr = int((time.time() if t is None else t) // step)
    h = hmac.new(key, struct.pack(">Q", ctr), hashlib.sha1).digest()
    o = h[-1] & 0xF
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def _aes256_ecb(data: bytes, key: str, decrypt=False) -> bytes:
    cmd = ["openssl", "enc", "-aes-256-ecb", "-K", key.encode().hex()]
    if decrypt:
        cmd.insert(2, "-d")
    r = subprocess.run(cmd, input=data, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode()[:200])
    return r.stdout


def make_token(t=None) -> str:
    """Fresh `data=` value. Pass t (unix seconds) only to reproduce a known one."""
    t = int(time.time() if t is None else t)
    plain = f"{t},{totp(t=t)}".encode()
    return base64.b64encode(_aes256_ecb(plain, OBF_KEY)).decode()


def read_token(b64: str) -> tuple:
    """(unix_seconds, totp) from an existing token -- used to verify the port."""
    out = _aes256_ecb(base64.b64decode(b64), OBF_KEY, decrypt=True).decode()
    ts, code = out.split(",")
    return int(ts), code


def demo():
    # tokens captured from the browser earlier in this session
    captured = ["vCow9HOM49mNT8X/h975mo7BTD1DjkFJZMHM7GG1pkU=",
                "uDcLJ6ojryn17ONJpskxvdASIaFI9BoTFTmpMyb3cok=",
                "JM/jLHOWoB4ZYA2uTdNZbvLlPpoT3luXJlsuJxJJIYg="]
    for b in captured:
        ts, code = read_token(b)
        assert 1_700_000_000 < ts < 2_000_000_000, ts
        # the real proof: our TOTP reproduces the code the browser generated
        assert totp(t=ts) == code, f"TOTP mismatch at {ts}: theirs={code} ours={totp(t=ts)}"
        # and re-encrypting that timestamp reproduces the token byte for byte
        assert make_token(t=ts) == b, f"round-trip failed for {b}"
        print(f"  ok  ts={ts}  totp={code}  age={(time.time()-ts)/60:6.0f} min")
    print("self-check ok -- port reproduces the browser's tokens exactly")


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["--test"]:
        demo()
    else:
        print(make_token())
