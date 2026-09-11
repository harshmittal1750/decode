"""Liquidation-heatmap analysis: how much gets triggered by a move to price X.

The heatmap has no long/short flag. Side is inferred from the fact that levels
decay once price sweeps them, so anything still standing BELOW spot is longs and
anything ABOVE is shorts.

Beware raw skew: the window is not symmetric around spot, so "more dollars
below" is partly just more price space below. per_level_skew() divides that out.
"""
from __future__ import annotations

from typing import Iterable

from .. import fmt


def book(row: dict) -> tuple[float, dict[float, float]]:
    """(spot, {price_level: standing_usd}) from a processed heatmap row."""
    return row["spot"], {float(p): float(v) for p, v in row["levels"]}


def between(spot: float, levels: dict[float, float], target: float) -> dict:
    """Liquidations triggered by a move from spot to target."""
    lo, hi = sorted((spot, target))
    hit = {p: v for p, v in levels.items() if lo <= p <= hi}
    return {"side": "short" if target > spot else "long",
            "total": sum(hit.values()),
            "levels": sorted(hit.items())}


def at(levels: dict[float, float], target: float) -> tuple[float, float]:
    """Standing liquidity in the single bucket nearest target."""
    p = min(levels, key=lambda p: abs(p - target))
    return p, levels[p]


def skew(spot: float, levels: dict[float, float]) -> dict:
    """Raw and per-level skew.

    Raw skew is contaminated by window geometry: with spot at 79k in a 49k-94k
    window there is 37% of price space below and 20% above, giving ~1.7x more
    level slots below before any positioning enters. per_level divides that out.
    """
    below = {p: v for p, v in levels.items() if p < spot}
    above = {p: v for p, v in levels.items() if p > spot}
    if not below or not above:
        return {"raw": None, "per_level": None,
                "below_usd": sum(below.values()), "above_usd": sum(above.values())}
    b, a = sum(below.values()), sum(above.values())
    return {"raw": b / a,
            "per_level": (b / len(below)) / (a / len(above)),
            "below_usd": b, "above_usd": a,
            "n_below": len(below), "n_above": len(above)}


def fuel_within(spot: float, levels: dict[float, float], pct: float = 0.05) -> float:
    """Standing liquidity within +/-pct of spot -- the part that actually churns.

    Measured across two pulls 17h apart: 20 of 23 levels that moved were inside
    5% of spot. Beyond 10%, the book is effectively frozen.
    """
    return sum(v for p, v in levels.items() if abs(p - spot) / spot <= pct)


def zones(row: dict, gap_mult: float = 1.5) -> list[dict]:
    """Contiguous price bands of standing liquidity, ranked by size.

    Ranking individual buckets is misleading: a wall spread over eight adjacent
    buckets loses to a single fat one, even when the wall is five times larger.
    Levels are merged into a zone while the gap between them stays within
    `gap_mult` x the median bucket spacing, so the unit of measure is the band
    price actually has to cross rather than an artifact of the grid.

    Distance is measured to the NEAR edge -- that is where price meets the zone.
    """
    spot, levels = book(row)
    if len(levels) < 2:
        return []
    pts = sorted(levels.items())
    gaps = sorted(b[0] - a[0] for a, b in zip(pts, pts[1:]))
    step = gaps[len(gaps) // 2] or 1.0            # median spacing, robust to holes
    tol = step * gap_mult
    total = sum(levels.values())

    groups: list[list[tuple[float, float]]] = [[pts[0]]]
    for prev, cur in zip(pts, pts[1:]):
        (groups[-1].append(cur) if cur[0] - prev[0] <= tol else groups.append([cur]))

    out = []
    for g in groups:
        usd = sum(v for _, v in g)
        lo, hi = g[0][0], g[-1][0]
        peak_price, peak_usd = max(g, key=lambda kv: kv[1])
        near = lo if lo > spot else (hi if hi < spot else spot)
        c_lo, c_hi, c_usd, c_n = _core(g, usd)
        out.append({
            "lo": lo, "hi": hi, "usd": usd, "n": len(g),
            # Where the mass actually sits. In a densely populated book the
            # gap-merge above cannot split anything -- every bucket is occupied,
            # so one peak with a long tail comes out as a single 8%-wide "zone".
            # The core is the narrowest span holding CORE_FRAC of that mass, and
            # it is what gets reported: on the 6m book a $6,900 zone turned out
            # to be a $1,055 wall plus tail.
            "core_lo": c_lo, "core_hi": c_hi, "core_usd": c_usd, "core_n": c_n,
            "core_frac_of_width": (c_hi - c_lo) / (hi - lo) if hi > lo else 1.0,
            "pct_of_book": usd / total * 100,
            "side": "short" if lo > spot else "long" if hi < spot else "straddles",
            "dist_pct": (near - spot) / spot * 100,
            "peak_price": peak_price, "peak_usd": peak_usd,
        })
    return sorted(out, key=lambda z: -z["usd"])


CORE_FRAC = 0.5


def _core(g: list[tuple[float, float]], total: float, frac: float = CORE_FRAC):
    """Narrowest contiguous span of `g` holding `frac` of its liquidity."""
    best = None
    for i in range(len(g)):
        acc = 0.0
        for j in range(i, len(g)):
            acc += g[j][1]
            if acc >= total * frac:
                width = g[j][0] - g[i][0]
                if best is None or width < best[0]:
                    best = (width, g[i][0], g[j][0], acc, j - i + 1)
                break
    if best is None:                     # single bucket, or frac unreachable
        return g[0][0], g[-1][0], total, len(g)
    _, lo, hi, acc, n = best
    return lo, hi, acc, n


def _nice_step(span: float, target_rows: int = 55) -> float:
    """A round bucket size that lands near `target_rows` rows."""
    raw = span / max(target_rows, 1)
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    for m in (1, 2, 2.5, 5, 10):
        if raw <= mag * m:
            return mag * m
    return mag * 10


def grid(books: dict[str, dict], step: float | None = None,
         lo: float | None = None, hi: float | None = None) -> dict:
    """Bucket every window's book onto ONE shared price ladder.

    Rows are a continuous range at a fixed step -- including buckets where a
    window holds nothing. An empty cell is information: it says price can pass
    that level without meeting forced flow, which a sparse list of only-populated
    levels hides.
    """
    all_levels = {w: dict(book(r)[1]) for w, r in books.items()}
    prices = [p for lv in all_levels.values() for p in lv]
    if not prices:
        return {"step": 0, "rows": [], "windows": list(books)}
    lo = min(prices) if lo is None else lo
    hi = max(prices) if hi is None else hi
    step = step or _nice_step(hi - lo)

    n = int((hi - lo) / step) + 1
    rows = []
    for i in range(n):
        bl = lo + i * step
        cells = {w: sum(v for p, v in lv.items() if bl <= p < bl + step)
                 for w, lv in all_levels.items()}
        rows.append({"lo": bl, "hi": bl + step, "cells": cells,
                     "total": sum(cells.values())})
    return {"step": step, "lo": lo, "hi": hi, "rows": rows, "windows": list(books)}


def grid_report(books: dict[str, dict], spot: float, step: float | None = None,
                span_pct: float | None = None, hide_empty: bool = False) -> str:
    """Cross-timeframe price ladder. Empty buckets are shown, not skipped."""
    lo = hi = None
    if span_pct:
        lo, hi = spot * (1 - span_pct / 100), spot * (1 + span_pct / 100)
    g = grid(books, step, lo, hi)
    if not g["rows"]:
        return "no levels"
    wins = g["windows"]
    peak = max((r["total"] for r in g["rows"]), default=1) or 1

    rows, spot_at = [], None
    for r in g["rows"]:
        if hide_empty and r["total"] == 0:
            continue
        if spot_at is None and r["hi"] > spot:
            spot_at = len(rows) - 1
        cells = [("·" if r["cells"][w] == 0 else f"{r['cells'][w]/1e6:,.0f}") for w in wins]
        bar = "▇" * round(r["total"] / peak * 16) if r["total"] else ""
        rows.append([fmt.band(r["lo"], r["hi"] - 1),
                     fmt.pct((r["lo"] - spot) / spot * 100, 1),
                     *cells,
                     f"{r['total']/1e6:,.0f}" if r["total"] else "·", bar])

    cols = ([("Price bucket", ">"), ("From spot", ">")]
            + [(w.upper(), ">") for w in wins]
            + [("Total", ">"), ("", "<")])
    return "\n".join([
        fmt.heading("LIQUIDITY BY PRICE LEVEL"),
        "",
        fmt.kv([("Spot", fmt.price(spot)),
                ("Bucket size", fmt.price(g["step"])),
                ("Rows", f"{len(rows)}  ({fmt.price(g['lo'])} to {fmt.price(g['hi'])})")],
               indent=2),
        "",
        fmt.table(cols, rows, indent=2, rule_after=spot_at),
        "",
        "  Figures are $ millions of standing liquidation liquidity per bucket.",
        "  '·' means nothing there: price crosses that level meeting no forced flow.",
        "  The dotted rule marks spot. Below it = longs, above it = shorts.",
    ])


def ladder(row: dict, side: str, gap_mult: float = 1.5) -> list[dict]:
    """Zones on one side of spot, ordered by how soon price reaches them.

    `side` is "short" (above spot) or "long" (below). Each entry carries the
    cumulative liquidation price would have triggered by the time it finishes
    crossing that zone, plus the gap to the previous one -- a small gap is what
    lets one zone's forced flow carry price into the next.
    """
    zs = [z for z in zones(row, gap_mult) if z["side"] in (side, "straddles")]
    zs.sort(key=lambda z: abs(z["dist_pct"]))
    cum = 0.0
    prev_far = None
    for z in zs:
        cum += z["usd"]
        z["cum_usd"] = cum
        far = z["hi"] if side == "short" else z["lo"]
        z["gap_pct"] = None if prev_far is None else abs(z["dist_pct"]) - prev_far
        prev_far = abs((far - row["spot"]) / row["spot"] * 100)
    return zs


def summary_table(books: dict[str, dict], gap_mult: float = 1.5) -> str:
    """One row per timeframe: how big each side is and where its mass sits."""
    rows = []
    for w, row in books.items():
        spot, levels = book(row)
        sk = skew(spot, levels)

        def core_of(side):
            lad = ladder(row, side, gap_mult)
            if not lad:
                return "-", "-"
            z = max(lad, key=lambda z: z["usd"])      # where the MASS is
            return fmt.band(z["core_lo"], z["core_hi"]), fmt.pct(z["dist_pct"])

        up_band, up_away = core_of("short")
        dn_band, dn_away = core_of("long")
        rows.append([w.upper(), fmt.price(spot), fmt.money(sum(levels.values())),
                     fmt.money(sk["above_usd"]), up_band, up_away,
                     fmt.money(sk["below_usd"]), dn_band, dn_away])

    return "\n".join([
        fmt.heading("LIQUIDATION MAP BY TIMEFRAME"),
        "",
        fmt.table(
            [("Window", "<"), ("Spot", ">"), ("Book", ">"),
             ("Shorts above", ">"), ("Where most shorts sit", ">"), ("Away", ">"),
             ("Longs below", ">"), ("Where most longs sit", ">"), ("Away", ">")],
            rows, indent=2),
        "",
        "  Shorts liquidate as price RISES  (forced buying).",
        "  Longs  liquidate as price FALLS  (forced selling).",
        "  'Where most X sit' = narrowest band holding half that side's largest zone.",
        "  Longer windows hold more history, so their books are naturally larger.",
    ])


def walls_report(row: dict, top: int = 6, gap_mult: float = 1.5) -> str:
    """Where each side's positions sit, and what their liquidation forces."""
    spot, levels = book(row)
    if len(levels) < 2:
        return "not enough levels"
    out = [f"spot ${spot:,.0f}   {len(levels)} levels   book ${sum(levels.values())/1e9:.2f}B"]

    for side, arrow, forced in (("short", "UP  ", "forced BUYING  -> pushes price higher"),
                                ("long", "DOWN", "forced SELLING -> pushes price lower")):
        lad = ladder(row, side, gap_mult)
        tot = sum(z["usd"] for z in lad)
        out += ["", f"{arrow} price rises into SHORTS" if side == "short"
                else f"{arrow} price falls into LONGS",
                f"     liquidating them = {forced}",
                f"     ${tot/1e9:.2f}B of {side} liquidity in {len(lad)} zones", ""]
        if not lad:
            out.append("     (none)")
            continue
        out.append(f"     {'reach':>7}  {'core (half the mass)':>21} {'in core':>9} "
                   f"{'in zone':>9} {'cumul':>9}  {'spans':>17}")
        for z in lad[:top]:
            core = (f"${z['core_lo']:,.0f}" if z["core_n"] == 1
                    else f"${z['core_lo']:,.0f}-${z['core_hi']:,.0f}")
            # only show the full support when it is materially wider than the core
            spans = ("" if z["core_frac_of_width"] > 0.6
                     else f"${z['lo']:,.0f}-${z['hi']:,.0f}")
            chain = " <-chains" if (z["gap_pct"] is not None and z["gap_pct"] < 1.0) else ""
            out.append(f"     {z['dist_pct']:>+6.1f}%  {core:>21} "
                       f"{'$' + format(z['core_usd']/1e6, ',.0f') + 'M':>9} "
                       f"{'$' + format(z['usd']/1e6, ',.0f') + 'M':>9} "
                       f"{'$' + format(z['cum_usd']/1e6, ',.0f') + 'M':>9}  {spans:>17}{chain}")
        big = max(lad, key=lambda z: z["usd"])
        tail = ("" if big["core_frac_of_width"] > 0.6 else
                f" (with a tail out to ${big['hi']:,.0f})" if side == "short" else
                f" (with a tail down to ${big['lo']:,.0f})")
        big_core = (f"${big['core_lo']:,.0f}" if big["core_n"] == 1
                    else f"${big['core_lo']:,.0f}-${big['core_hi']:,.0f}")
        out += ["",
                f"     MOST {side}s sit at {big_core} "
                f"({big['dist_pct']:+.1f}% away): ${big['core_usd']/1e6:,.0f}M{tail}",
                f"     crossing the whole zone triggers ${big['cum_usd']/1e6:,.0f}M "
                f"cumulative -- {forced.split(' ->')[0].strip().lower()}"]

    out += ["", "'gap' is the distance from the previous zone's far edge. Under ~1%",
            "means one zone's forced flow can carry price into the next.",
            "This is where a move would ACCELERATE, not a forecast that it happens."]
    return "\n".join(out)


def chart(row: dict, width: int = 46) -> str:
    """The whole book, ascending by price, as a text bar chart.

    Rendered low-to-high so it reads like the site's own heatmap axis. Spot is
    marked in place, which is what makes the two sides legible: everything below
    the marker is long liquidations, everything above is shorts.
    """
    spot, levels = book(row)
    if not levels:
        return "empty book"
    rows = sorted(levels.items())
    peak = max(levels.values())
    total = sum(levels.values())
    sk = skew(spot, levels)

    out = [f"spot ${spot:,.0f}   {len(rows)} levels   "
           f"${rows[0][0]:,.0f} - ${rows[-1][0]:,.0f}   total ${total/1e9:.2f}B",
           f"longs below ${sk['below_usd']/1e9:.2f}B   shorts above ${sk['above_usd']/1e9:.2f}B"
           + (f"   per-level skew {sk['per_level']:.2f}x" if sk["per_level"] else ""),
           ""]

    cum = 0.0
    marked = False
    for price, usd in rows:
        if not marked and price > spot:
            out.append(f"{'':>10}  {'':>9}  {'-' * width}  <-- SPOT ${spot:,.0f}")
            marked = True
        cum += usd
        bar = "#" * max(1, round(usd / peak * width))
        side = "S" if price > spot else "L"
        # keep the $ glued to its number so columns stay parseable
        out.append(f"{'$' + format(price, ',.0f'):>10} {side} "
                   f"{'$' + format(usd / 1e6, ',.1f') + 'M':>10}  {bar:<{width}} "
                   f"{cum / total * 100:>5.1f}%")
    if not marked:                      # spot sits above every level in the book
        out.append(f"{'':>10}  {'':>9}  {'-' * width}  <-- SPOT ${spot:,.0f}")
    out += ["", "L = longs liquidate on the way down, S = shorts on the way up.",
            "Last column is cumulative share of the book from the bottom up."]
    return "\n".join(out)


def report(row: dict, targets: Iterable[float]) -> str:
    spot, levels = book(row)
    lo, hi = min(levels), max(levels)
    sk = skew(spot, levels)
    out = [f"spot ${spot:,.0f}   heatmap ${lo:,.0f}-${hi:,.0f}   {len(levels)} levels",
           f"skew raw {sk['raw']:.2f}x  per-level {sk['per_level']:.2f}x   "
           f"fuel +/-5% ${fuel_within(spot, levels)/1e9:.2f}B",
           f"longs (below spot) ${sk['below_usd']/1e9:.2f}B   "
           f"shorts (above spot) ${sk['above_usd']/1e9:.2f}B", ""]
    for t in targets:
        r = between(spot, levels, t)
        bp, bv = at(levels, t)
        warn = "  [OUTSIDE RANGE - undercounted]" if not lo <= t <= hi else ""
        top = sorted(r["levels"], key=lambda kv: -kv[1])[:4]
        out += [f"${t:,.0f}  ({r['side']}s){warn}",
                f"  cumulative : ${r['total']/1e9:,.3f}B across {len(r['levels'])} levels",
                f"  at level   : ${bv/1e6:,.1f}M (bucket ${bp:,.0f})",
                "  clusters   : " + ", ".join(f"${p:,.0f}=${v/1e6:.0f}M" for p, v in top), ""]
    return "\n".join(out)
