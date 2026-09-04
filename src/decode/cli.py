"""decode <command> -- collect, inspect, debug and analyse the archive."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys

from . import config, pipeline, session, store
from .analysis import liq, pressure


def _ts(ms) -> str:
    if not ms:
        return "-"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def cmd_collect(args, conn) -> int:
    obe = args.obe or session.read_obe()
    result = pipeline.run_once(conn, obe=obe)
    print(result.summary)
    if any("api 4000" in msg for msg in result.failed.values()):
        print("looks like an API-level rejection -- try: decode login", file=sys.stderr)
    if args.sweep:
        n = store.sweep_raw(conn)
        print(f"swept {n} raw blobs older than {config.RAW_RETENTION_DAYS}d")
    # exit 0 unless EVERYTHING failed: independent streams, one gap is not an outage
    return 0 if result.ok else 1


def cmd_login(args, conn) -> int:
    from . import login
    try:
        login.capture()
    except ImportError:
        print("playwright not installed -- run: uv sync --group browser", file=sys.stderr)
        return 1
    print(f"session captured -- saved to {config.SESSION_PATH}")
    return 0


def cmd_serve(args, conn) -> int:
    import uvicorn
    uvicorn.run("decode.api:app", host="0.0.0.0", port=args.port, reload=args.reload)
    return 0


def cmd_status(args, conn) -> int:
    s = store.stats(conn)
    print(f"db        {config.DB_PATH}")
    print(f"runs      {s['runs']}   {_ts(s['first_run'])} -> {_ts(s['last_run'])}")
    print(f"processed {s['processed_rows']} rows   raw {s['raw_rows']}   errors {s['errors']}")
    print(f"streams   {', '.join(s['streams']) or '-'}")
    for name in s["streams"]:
        row = store.latest(conn, name)
        n = len(store.series(conn, name))
        print(f"  {name:10} {n:4} rows   latest {_ts(row['fetched_at'])}")
    return 0


def cmd_errors(args, conn) -> int:
    rows = store.recent_errors(conn, args.limit, args.stream)
    if not rows:
        print("no errors recorded")
        return 0
    for r in rows:
        print(f"{_ts(r['occurred_at'])}  run={r['run_id']}  {r['stream']:10} {r['message']}")
        if args.traceback and r["traceback"]:
            print("    " + r["traceback"].replace("\n", "\n    ").rstrip())
    return 0


def cmd_raw(args, conn) -> int:
    body = store.raw_body(conn, args.run_id, args.stream)
    if body is None:
        print(f"no raw for run {args.run_id} stream {args.stream} "
              f"(expires after {config.RAW_RETENTION_DAYS}d)", file=sys.stderr)
        return 1
    print(json.dumps(json.loads(body), indent=2)[:args.chars])
    return 0


def cmd_replay(args, conn) -> int:
    """Re-run a reducer over archived raw -- fix code, see what it would produce."""
    print(json.dumps(pipeline.replay(conn, args.run_id, args.stream), indent=2)[:args.chars])
    return 0


def cmd_liq(args, conn) -> int:
    row = store.latest(conn, "heatmap")
    if not row:
        print("no heatmap rows yet -- run: decode collect", file=sys.stderr)
        return 1
    print(liq.report(row, args.targets or [85000, 60000]))
    return 0


def cmd_pressure(args, conn) -> int:
    rows = store.aligned(conn)
    if len(rows) < 4:
        print(f"only {len(rows)} aligned rows; pressure needs >=4. "
              "Keep collecting -- this builds up over runs.", file=sys.stderr)
        return 1
    print(pressure.report(rows))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="decode", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--db", default=None, help="override database path")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="fetch all streams into the archive")
    c.add_argument("--obe", default="", help="optional session cookie (not required)")
    c.add_argument("--sweep", action="store_true", help="also expire old raw blobs")
    c.set_defaults(fn=cmd_collect)

    sub.add_parser("status", help="archive overview").set_defaults(fn=cmd_status)

    e = sub.add_parser("errors", help="recent failures with tracebacks")
    e.add_argument("-n", "--limit", type=int, default=20)
    e.add_argument("-s", "--stream")
    e.add_argument("-t", "--traceback", action="store_true")
    e.set_defaults(fn=cmd_errors)

    r = sub.add_parser("raw", help="dump archived raw payload for one run/stream")
    r.add_argument("run_id", type=int)
    r.add_argument("stream")
    r.add_argument("--chars", type=int, default=4000)
    r.set_defaults(fn=cmd_raw)

    rp = sub.add_parser("replay", help="re-run a reducer over archived raw")
    rp.add_argument("run_id", type=int)
    rp.add_argument("stream")
    rp.add_argument("--chars", type=int, default=4000)
    rp.set_defaults(fn=cmd_replay)

    l = sub.add_parser("liq", help="liquidation fuel to given prices")
    l.add_argument("targets", nargs="*", type=float)
    l.set_defaults(fn=cmd_liq)

    sub.add_parser("pressure", help="spot vs futures decomposition").set_defaults(fn=cmd_pressure)

    sub.add_parser("login", help="capture a fresh obe session via browser login"
                    ).set_defaults(fn=cmd_login)

    sv = sub.add_parser("serve", help="run the read-only API over the archive")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")
    sv.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(config.LOG_PATH)],
    )
    conn = store.connect(args.db or config.DB_PATH)
    try:
        return args.fn(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
