"""Read-only HTTP API over the archive. `decode serve` runs this.

Every route reuses store.py / analysis functions directly -- no business logic
lives here, only JSON shaping. That is deliberate: the CLI and the dashboard must
never disagree, so both call the same functions and neither owns a private copy.

A fresh connection is opened per request rather than shared: traffic is a
personal dashboard, and a short-lived connection sidesteps SQLite's cross-thread
rules instead of working around them.
"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import config, store, streams
from .analysis import liq, pressure

app = FastAPI(title="decode", description="CoinGlass derivatives archive")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["GET"],
)

LEVEL_WINDOWS = ["24h", "3d", "1w", "2w", "1m", "3m", "6m"]


@contextmanager
def db():
    conn = store.connect(config.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def _book_row(conn, window: str) -> dict:
    if window not in streams.HEATMAP_WINDOWS:
        raise HTTPException(400, f"unknown window {window!r}; "
                                 f"try {sorted(streams.HEATMAP_WINDOWS)}")
    row = store.latest(conn, f"heatmap_{window}")
    if not row:
        raise HTTPException(404, f"nothing archived for {window} -- run: decode collect")
    return row


# --- meta --------------------------------------------------------------------

@app.get("/windows")
def windows():
    """Available heatmap windows, whether each is session-gated, and row counts."""
    with db() as conn:
        return [{"window": w, "gated": gated, "query": q,
                 "rows": len(store.series(conn, f"heatmap_{w}")),
                 "in_levels_view": w in LEVEL_WINDOWS}
                for w, (q, gated) in streams.HEATMAP_WINDOWS.items()]


@app.get("/status")
def status():
    with db() as conn:
        s = store.stats(conn)
        gated = streams.gated_streams()
        s["per_stream"] = {
            name: {"rows": len(store.series(conn, name)),
                   "latest_fetched_at": (store.latest(conn, name) or {}).get("fetched_at"),
                   "needs_session": name in gated}
            for name in s["streams"]
        }
        return s


@app.get("/runs")
def runs(limit: int = 50):
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))]


@app.get("/errors/recent")
def errors(limit: int = 20, stream: str | None = None):
    with db() as conn:
        return [dict(r) for r in store.recent_errors(conn, limit, stream)]


# --- liquidation map ---------------------------------------------------------

@app.get("/heatmap/{window}")
def heatmap(window: str):
    """The raw live book for one window: every level and its standing liquidity."""
    with db() as conn:
        row = _book_row(conn, window)
        spot, levels = liq.book(row)
        return {"window": window, "spot": spot, "fetched_at": row["fetched_at"],
                "run_id": row["run_id"], "range": row.get("range"),
                "skew": liq.skew(spot, levels),
                "fuel_within_5pct": liq.fuel_within(spot, levels),
                "levels": [{"price": p, "usd": v,
                            "side": "short" if p > spot else "long",
                            "dist_pct": (p - spot) / spot * 100}
                           for p, v in sorted(levels.items())]}


@app.get("/zones/{window}")
def zones(window: str, gap_mult: float = 1.5):
    """Merged liquidity zones with their core -- the band holding half the mass."""
    with db() as conn:
        row = _book_row(conn, window)
        return {"window": window, "spot": row["spot"],
                "zones": liq.zones(row, gap_mult)}


@app.get("/walls/{window}")
def walls(window: str, gap_mult: float = 1.5):
    """Both ladders: what price meets going up (shorts) and down (longs)."""
    with db() as conn:
        row = _book_row(conn, window)
        spot, levels = liq.book(row)
        out = {"window": window, "spot": spot, "fetched_at": row["fetched_at"]}
        for side in ("short", "long"):
            lad = liq.ladder(row, side, gap_mult)
            biggest = max(lad, key=lambda z: z["usd"]) if lad else None
            out[side] = {
                "total_usd": sum(z["usd"] for z in lad),
                "forces": "buying" if side == "short" else "selling",
                "biggest": biggest,
                "ladder": lad,
            }
        return out


@app.get("/liq/{window}")
def liq_targets(window: str, targets: str = Query("", description="comma-separated prices")):
    """Cumulative liquidation triggered by a move from spot to each target."""
    with db() as conn:
        row = _book_row(conn, window)
        spot, levels = liq.book(row)
        lo, hi = min(levels), max(levels)
        try:
            wanted = [float(t) for t in targets.split(",") if t.strip()]
        except ValueError:
            raise HTTPException(400, "targets must be comma-separated numbers")
        out = []
        for t in wanted:
            r = liq.between(spot, levels, t)
            bp, bv = liq.at(levels, t)
            out.append({"target": t, **r,
                        "levels": [{"price": p, "usd": v} for p, v in r["levels"]],
                        "at_bucket": {"price": bp, "usd": bv},
                        "outside_range": not (lo <= t <= hi)})
        return {"window": window, "spot": spot, "range": [lo, hi], "targets": out}


@app.get("/levels/grid")
def levels_grid(windows_: str = Query("", alias="windows"),
                step: float | None = None, span: float | None = None):
    """One shared price ladder across timeframes, INCLUDING empty buckets."""
    wins = [w.strip() for w in windows_.split(",") if w.strip()] or LEVEL_WINDOWS
    with db() as conn:
        books = {w: _book_row(conn, w) for w in wins}
        spot = next(iter(books.values()))["spot"]
        lo = hi = None
        if span:
            lo, hi = spot * (1 - span / 100), spot * (1 + span / 100)
        g = liq.grid(books, step, lo, hi)
        return {"spot": spot, "step": g["step"], "windows": g["windows"],
                "rows": [{"lo": r["lo"], "hi": r["hi"], "total": r["total"],
                          "dist_pct": (r["lo"] - spot) / spot * 100,
                          "cells": r["cells"]} for r in g["rows"]]}


@app.get("/levels/summary")
def levels_summary(windows_: str = Query("", alias="windows"), gap_mult: float = 1.5):
    """Per-timeframe: book size, both sides, and where each side's mass sits."""
    wins = [w.strip() for w in windows_.split(",") if w.strip()] or LEVEL_WINDOWS
    with db() as conn:
        out = []
        for w in wins:
            row = _book_row(conn, w)
            spot, levels = liq.book(row)

            def core(side):
                lad = liq.ladder(row, side, gap_mult)
                if not lad:
                    return None
                z = max(lad, key=lambda z: z["usd"])
                return {"lo": z["core_lo"], "hi": z["core_hi"], "usd": z["core_usd"],
                        "dist_pct": z["dist_pct"], "zone_usd": z["usd"],
                        "zone_lo": z["lo"], "zone_hi": z["hi"]}

            out.append({"window": w, "spot": spot, "fetched_at": row["fetched_at"],
                        "book_usd": sum(levels.values()), "n_levels": len(levels),
                        "skew": liq.skew(spot, levels),
                        "fuel_within_5pct": liq.fuel_within(spot, levels),
                        "short_core": core("short"), "long_core": core("long")})
        return out


# --- spot vs futures ---------------------------------------------------------

@app.get("/pressure")
def pressure_():
    with db() as conn:
        rows = store.aligned(conn)
        if len(rows) < 4:
            raise HTTPException(409, f"only {len(rows)} aligned rows; need >=4. "
                                     "Keep collecting.")
        d = pressure.decompose(rows)
        d["series"] = [{"ts": r["ts"], "spot": r["spot"], "perp": r["perp"],
                        "basis_pct": r["basis_pct"]} for r in rows]
        # Daily attribution: was a futures-led move forced (liquidations) or
        # fresh positioning? Needs realised liquidations, which are daily-only.
        lh = store.latest(conn, "liqhist")
        d["attribution"] = pressure.attribute(rows, (lh or {}).get("series", []))
        return d


@app.get("/funding")
def funding():
    """Current funding per venue. Deliberately NOT averaged -- venues are not
    comparable (different intervals, caps, and stale-value behaviour)."""
    with db() as conn:
        row = store.latest(conn, "funding")
        if not row:
            raise HTTPException(404, "no funding rows yet")
        rates = row.get("rates", {})
        return {"ts": row.get("ts"), "spot": row.get("spot"),
                "venues": [{"venue": k, "rate_pct_8h": v,
                            "annualised_pct": (v * 3 * 365) if v is not None else None}
                           for k, v in sorted(rates.items(), key=lambda kv: -(kv[1] or 0))],
                "series": row.get("series", [])}


@app.get("/longshort")
def longshort():
    with db() as conn:
        row = store.latest(conn, "longshort")
        if not row:
            raise HTTPException(404, "no longshort rows yet")
        return {"fetched_at": row["fetched_at"],
                "venues": [{"venue": k, **v} for k, v in row.get("venues", {}).items()]}


@app.get("/liqtoday")
def liqtoday():
    with db() as conn:
        row = store.latest(conn, "liqtoday")
        if not row:
            raise HTTPException(404, "no liqtoday rows yet")
        return row


@app.get("/series/{stream}")
def series(stream: str, limit: int = 200):
    """Raw processed history for any stream."""
    with db() as conn:
        rows = store.series(conn, stream, limit=limit)
        if not rows:
            raise HTTPException(404, f"no rows for stream {stream!r}")
        return rows
