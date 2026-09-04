"""decode <command> -- collect, inspect, debug and analyse the archive."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys

from . import config, pipeline, session, store, streams
from .analysis import liq, pressure


DEFAULT_LEVEL_WINDOWS = ["24h", "3d", "1w", "2w", "1m", "3m", "6m"]


def _ts(ms) -> str:
    if not ms:
        return "-"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def cmd_collect(args, conn) -> int:
    obe = args.obe or session.read_obe()
    result = pipeline.run_once(conn, obe=obe)
    print(result.summary)
    if args.sweep:
        n = store.sweep_raw(conn)
        print(f"swept {n} raw blobs older than {config.RAW_RETENTION_DAYS}d")

    # Levels are DERIVED, never stored: they recompute from the heatmap rows this
    # run just wrote, so improving the zone/core algorithm retroactively improves
    # every past run. Storing the table would freeze today's formula -- and it has
    # already changed twice (gap-merge -> core-of-mass).
    if not args.no_levels:
        books = {w: row for w in DEFAULT_LEVEL_WINDOWS
                 if (row := store.latest(conn, f"heatmap_{w}"))}
        if books:
            print()
            print(liq.summary_table(books))
            if args.full_levels:
                spot = next(iter(books.values()))["spot"]
                print()
                print(liq.grid_report(books, spot, span_pct=args.span))

    # Exit non-zero ONLY for failures that will not fix themselves, so cron mail
    # stays worth reading. Transient single-stream errors are already retried in
    # the run and again next run; they land in `errors` rather than paging.
    alarm = result.alarm()
    if alarm:
        print(f"ALARM: {alarm}", file=sys.stderr)
        return 1
    if result.failed:
        print(f"note: {len(result.failed)} stream(s) failed transiently "
              f"({' '.join(result.failed)}); see: decode errors", file=sys.stderr)
    return 0


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
    if args.live:
        import urllib.parse

        from . import client, streams, token
        obe = args.obe or session.read_obe()
        if streams.window_needs_session(args.window) and not obe:
            print(f"window {args.window!r} is session-gated and no session is stored; "
                  "run: decode login  (or pass --obe)", file=sys.stderr)
            return 1
        url = (streams.heatmap_url(args.window)
               + "&data=" + urllib.parse.quote(token.make_token(), safe=""))
        try:
            payload = client.fetch(url, obe=obe).payload
        except Exception as exc:
            print(f"fetch failed: {exc}", file=sys.stderr)
            return 1
        row = streams.r_heatmap(payload)
        print(f"[live {args.window}] {len(payload['liq'])} cells, "
              f"{len(payload['prices'])} bars")
    else:
        row = store.latest(conn, f"heatmap_{args.window}")
        if not row:
            print(f"nothing archived for window {args.window!r} -- run: decode collect",
                  file=sys.stderr)
            return 1
        print(f"[archived run {row['run_id']}  {_ts(row['fetched_at'])}]")
    if args.zones:
        print(liq.walls_report(row, top=args.top))
    elif args.targets:
        print(liq.report(row, args.targets))
    else:
        # No targets given -> render the whole book instead of guessing prices.
        print(liq.chart(row))
    return 0


def _books(args, conn) -> dict:
    """Collect one heatmap row per requested window, live or from the archive."""
    import urllib.parse

    from . import client, token
    wins = args.windows or DEFAULT_LEVEL_WINDOWS
    obe = args.obe or session.read_obe()
    books = {}
    for w in wins:
        if args.live:
            if streams.window_needs_session(w) and not obe:
                print(f"skip {w}: session-gated, run decode login", file=sys.stderr)
                continue
            url = (streams.heatmap_url(w)
                   + "&data=" + urllib.parse.quote(token.make_token(), safe=""))
            try:
                books[w] = streams.r_heatmap(client.fetch(url, obe=obe).payload)
            except Exception as exc:
                print(f"skip {w}: {exc}", file=sys.stderr)
        else:
            row = store.latest(conn, f"heatmap_{w}")
            if row:
                books[w] = row
            else:
                print(f"skip {w}: nothing archived (run: decode collect)", file=sys.stderr)
    return books


def cmd_levels(args, conn) -> int:
    books = _books(args, conn)
    if not books:
        print("no books available", file=sys.stderr)
        return 1
    spot = list(books.values())[0]["spot"]
    print(liq.summary_table(books))
    print()
    print(liq.grid_report(books, spot, step=args.step, span_pct=args.span,
                          hide_empty=args.hide_empty))
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
    c.add_argument("--no-levels", action="store_true",
                   help="skip the per-timeframe levels summary printed after collecting")
    c.add_argument("--full-levels", action="store_true",
                   help="also print the full cross-timeframe price ladder")
    c.add_argument("--span", type=float, default=8.0,
                   help="+/- %% around spot for --full-levels (default 8)")
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
    l.add_argument("--live", action="store_true", help="fetch now instead of reading the archive")
    l.add_argument("--window", default="24h", choices=sorted(streams.HEATMAP_WINDOWS),
                   help="which heatmap window to fetch with --live (default 24h)")
    l.add_argument("--obe", default="", help="session override for gated windows")
    l.add_argument("--zones", action="store_true",
                   help="show where each side's positions sit and what liquidation forces")
    l.add_argument("--top", type=int, default=6, help="zones per direction (--zones)")
    l.set_defaults(fn=cmd_liq)

    lv = sub.add_parser("levels", help="price ladder of liquidity across all timeframes")
    lv.add_argument("--live", action="store_true", help="fetch now instead of the archive")
    lv.add_argument("--windows", nargs="*", choices=sorted(streams.HEATMAP_WINDOWS),
                    help=f"default: {' '.join(DEFAULT_LEVEL_WINDOWS)}")
    lv.add_argument("--step", type=float, default=None, help="bucket size in $ (auto)")
    lv.add_argument("--span", type=float, default=None,
                    help="only show +/- this %% around spot")
    lv.add_argument("--hide-empty", action="store_true", help="drop all-empty rows")
    lv.add_argument("--obe", default="")
    lv.set_defaults(fn=cmd_levels)

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
