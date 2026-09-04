"""Liquidation-heatmap analysis: how much gets triggered by a move to price X.

The heatmap has no long/short flag. Side is inferred from the fact that levels
decay once price sweeps them, so anything still standing BELOW spot is longs and
anything ABOVE is shorts.

Beware raw skew: the window is not symmetric around spot, so "more dollars
below" is partly just more price space below. per_level_skew() divides that out.
"""
from __future__ import annotations

from typing import Iterable


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
        out.append(f"${price:>9,.0f} {side} ${usd/1e6:>8,.1f}M  {bar:<{width}} "
                   f"{cum/total*100:>5.1f}%")
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
