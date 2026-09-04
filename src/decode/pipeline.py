"""Orchestration: fetch -> raw -> reduce -> processed, with errors captured.

Every stream is independent. One failure never costs the others, and every
failure is recorded with its traceback so a run can be diagnosed later without
reproducing it. The three failure surfaces are distinguished on purpose:

    fetch    network / HTTP / API-level  -> nothing to archive
    decrypt  key or gzip problem         -> raw IS archived, so you can retry it
    reduce   our own parsing code broke  -> raw IS archived, so you can fix and replay
"""
from __future__ import annotations

import logging
import traceback
import urllib.parse
from dataclasses import dataclass, field

from . import client, config, store, token
from .streams import STREAMS, Stream

log = logging.getLogger("decode.pipeline")


@dataclass
class RunResult:
    run_id: int
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        line = f"run {self.run_id}: {len(self.ok)}/{len(self.ok) + len(self.failed)} ok"
        if self.ok:
            line += f"  [{' '.join(self.ok)}]"
        if self.failed:
            line += f"  FAILED: {' '.join(self.failed)}"
        return line


def _url_for(s: Stream) -> str:
    if not s.needs_token:
        return s.url
    # 30s TOTP: mint at call time, never reuse across a run
    return f"{s.url}&data={urllib.parse.quote(token.make_token(), safe='')}"


def run_once(conn, streams: dict[str, Stream] | None = None, *, obe: str = "",
             fetch=client.fetch) -> RunResult:
    streams = streams or STREAMS
    run_id = store.start_run(conn, note=f"streams={len(streams)}")
    result = RunResult(run_id=run_id)
    clock_checked = False

    for name, s in streams.items():
        try:
            fetched = fetch(_url_for(s), obe=obe)
        except Exception as exc:
            _fail(conn, run_id, name, "fetch", exc, result)
            continue

        # Archive raw before touching it. If the reducer explodes below, the
        # bytes that broke it are already durable.
        try:
            with store.transaction(conn):
                store.save_raw(conn, run_id, name, fetched)
        except Exception as exc:
            _fail(conn, run_id, name, "save_raw", exc, result)
            continue

        # One clock check per run, using the server's own Date header.
        if not clock_checked:
            clock_checked = True
            try:
                token.check_clock(fetched.server_epoch)
            except token.ClockSkewError as exc:
                store.save_error(conn, run_id, "_clock", "ClockSkewError", str(exc))
                log.warning("clock skew: %s", exc)

        try:
            data = s.reduce(fetched.payload)
        except Exception as exc:
            _fail(conn, run_id, name, "reduce", exc, result)
            continue

        try:
            with store.transaction(conn):
                store.save_processed(conn, run_id, name, data)
        except Exception as exc:
            _fail(conn, run_id, name, "save_processed", exc, result)
            continue

        result.ok.append(name)
        log.info("%s ok in %dms (attempt %d)", name, fetched.elapsed_ms, fetched.attempts)

    store.finish_run(conn, run_id, len(result.ok), len(result.failed))
    return result


def _fail(conn, run_id: int, stream: str, stage: str, exc: Exception, result: RunResult) -> None:
    msg = f"[{stage}] {type(exc).__name__}: {exc}"
    store.save_error(conn, run_id, stream, type(exc).__name__, msg,
                     "".join(traceback.format_exception(exc)))
    result.failed[stream] = msg
    log.error("%s %s", stream, msg)


def replay(conn, run_id: int, stream: str):
    """Re-run a reducer against archived raw bytes.

    This is why raw is stored: fix a reducer, replay it over history, and see
    what it would have produced -- without needing the API to still serve it.
    """
    import json
    body = store.raw_body(conn, run_id, stream)
    if body is None:
        raise KeyError(f"no raw archived for run {run_id} stream {stream} "
                       f"(raw expires after {config.RAW_RETENTION_DAYS} days)")
    return STREAMS[stream].reduce(json.loads(body))
