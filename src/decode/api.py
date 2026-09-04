"""Read-only HTTP API over the archive. `decode serve` runs this.

Every route reuses store.py/analysis functions directly -- no business logic
lives here, only JSON shaping. A fresh connection is opened per request rather
than shared across the app: traffic here is a personal dashboard (low
volume), and a short-lived connection sidesteps SQLite's cross-thread
connection rules entirely instead of working around them.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import config, store
from .analysis import liq, pressure

app = FastAPI(title="decode")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET"],
)


@app.get("/status")
def status():
    conn = store.connect(config.DB_PATH)
    try:
        s = store.stats(conn)
        s["per_stream"] = {
            name: {"rows": len(store.series(conn, name)),
                   "latest_fetched_at": store.latest(conn, name)["fetched_at"]}
            for name in s["streams"]
        }
        return s
    finally:
        conn.close()


@app.get("/heatmap")
def heatmap():
    conn = store.connect(config.DB_PATH)
    try:
        row = store.latest(conn, "heatmap")
        if not row:
            raise HTTPException(404, "no heatmap rows yet -- run: decode collect")
        return row
    finally:
        conn.close()


@app.get("/liq")
def liq_(targets: str = Query("", description="comma-separated target prices")):
    conn = store.connect(config.DB_PATH)
    try:
        row = store.latest(conn, "heatmap")
        if not row:
            raise HTTPException(404, "no heatmap rows yet -- run: decode collect")
        spot, levels = liq.book(row)
        lo, hi = min(levels), max(levels)
        out = {
            "spot": spot, "range": [lo, hi], "n_levels": len(levels),
            "skew": liq.skew(spot, levels),
            "fuel_5pct": liq.fuel_within(spot, levels),
            "targets": [],
        }
        for t in (float(x) for x in targets.split(",") if x.strip()):
            r = liq.between(spot, levels, t)
            bp, bv = liq.at(levels, t)
            out["targets"].append({
                "target": t, "side": r["side"], "cumulative": r["total"],
                "in_range": lo <= t <= hi,
                "at_level": {"price": bp, "value": bv},
                "top_clusters": sorted(r["levels"], key=lambda kv: -kv[1])[:4],
            })
        return out
    finally:
        conn.close()


@app.get("/pressure")
def pressure_():
    conn = store.connect(config.DB_PATH)
    try:
        rows = store.aligned(conn)
        if len(rows) < 4:
            raise HTTPException(400, f"only {len(rows)} aligned rows; pressure needs >=4")
        return pressure.decompose(rows)
    finally:
        conn.close()


@app.get("/errors/recent")
def errors(limit: int = 20, stream: str | None = None):
    conn = store.connect(config.DB_PATH)
    try:
        return [dict(r) for r in store.recent_errors(conn, limit, stream)]
    finally:
        conn.close()


@app.get("/{stream}")
def raw_stream(stream: str):
    if stream not in ("longshort", "funding", "basis", "liqtoday"):
        raise HTTPException(404, "unknown stream")
    conn = store.connect(config.DB_PATH)
    try:
        row = store.latest(conn, stream)
        if not row:
            raise HTTPException(404, f"no {stream} rows yet -- run: decode collect")
        return row
    finally:
        conn.close()
