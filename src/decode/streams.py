"""Stream definitions: what to fetch, and what to keep from each payload.

`replayable` is the field that drives retention. An endpoint that returns its
own history on every call can be re-fetched if a reducer turns out to be wrong,
so we keep only a slim processed row. The liq heatmap cannot -- swept levels
decay out of the payload and exist nowhere else -- so its reducer keeps the
whole live book, and that is the one row we can never regenerate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .config import API

# How many trailing points of the funding/basis series to keep per row. Both
# endpoints return 180d on every call, so this is not the archive of record --
# it is enough tail for the analyses to work from day one (90 x 8h = 30 days)
# without re-fetching, at roughly 2KB per row.
SERIES_TAIL = 90

# Named heatmap windows, lifted verbatim from the site's own onChangeTime handler
# (chunk 12681 / 17190) rather than guessed -- the real limits are irregular
# (1w is 30m x 336, 2w is 30m x 672, 1mo is h2 x 372) and guessing gets them wrong.
#
#   site key -> (interval, limit)
#   h12 -> 5,144   d1 -> 5,288    h48 -> 15,192   d3  -> 15,288
#   w1  -> 30,336  w2 -> 30,672   mo1 -> h2,372   mo3 -> h6,360   mo6 -> h12,360
#
# `gated` = returns 40000 without a valid login session; measured, not assumed.
HEATMAP_WINDOWS = {
    "12h": ("interval=5&limit=144", False),
    "24h": ("interval=5&limit=288", False),
    "48h": ("interval=15&limit=192", False),
    "3d": ("interval=15&limit=288", False),
    "1w": ("interval=30&limit=336", True),
    "2w": ("interval=30&limit=672", True),
    "1m": ("interval=h2&limit=372", True),
    "3m": ("interval=h6&limit=360", True),
    "6m": ("interval=h12&limit=360", True),
}
DEFAULT_WINDOW = "24h"


def heatmap_url(window: str = DEFAULT_WINDOW) -> str:
    try:
        q, _ = HEATMAP_WINDOWS[window]
    except KeyError:
        raise KeyError(f"unknown window {window!r}; try {sorted(HEATMAP_WINDOWS)}") from None
    return f"{API}/index/aggregate/liqHeatMap?merge=true&symbol=BTC&{q}"


def window_needs_session(window: str) -> bool:
    return HEATMAP_WINDOWS.get(window, (None, False))[1]


@dataclass(frozen=True)
class Stream:
    name: str
    url: str
    reduce: Callable[[Any], dict]
    replayable: bool
    needs_token: bool = False
    needs_session: bool = False     # 40000 without a valid login; drives alerting
    note: str = ""


# --- reducers ----------------------------------------------------------------

def r_funding(d: Any) -> dict:
    """Current funding across venues + the spot index price.

    Venues are NOT comparable: funding intervals differ (Hyperliquid is hourly,
    Binance 8h), caps differ, and some venues repeat a stale value most of the
    time. Store per-venue and let the analysis decide -- never pre-average.
    """
    i = -1
    return {"ts": d["dateList"][i], "spot": d["priceList"][i],
            "rates": {ex: v[i] for ex, v in d["dataMap"].items()},
            # Series tail, so basis can be joined on the 8h GRID. The last
            # element alone is useless for that: it carries a ragged "now"
            # timestamp while basis's last element sits on the grid, and pairing
            # those two produces a price change masquerading as a basis.
            "series": list(zip(d["dateList"][-SERIES_TAIL:], d["priceList"][-SERIES_TAIL:]))}


def r_basis(d: Any) -> dict:
    """Perp + quarterly futures prices.

    NB: this dateList is offset one bar from funding's. Do not reconcile it here
    -- store.aligned() owns that join. See store.py.
    """
    perp = next((s for s in d["data"] if s["instrumentId"].endswith("PERP")), None)
    return {"ts": d["dateList"][-1],
            "px": {s["instrumentId"]: s["priceList"][-1] for s in d["data"]},
            "series": list(zip(d["dateList"][-SERIES_TAIL:],
                               perp["priceList"][-SERIES_TAIL:])) if perp else []}


def r_heatmap(d: Any) -> dict:
    """The live book: the newest time column only.

    Values are standing liquidity carried forward through time and decaying when
    price sweeps a level, so only the last column is the live book -- summing all
    columns would count the same money once per bar. The older columns are
    dropped because they are already reflected in the newest one.
    """
    t_now = max(c[0] for c in d["liq"])
    y = d["y"]
    prices = d["prices"]
    return {"bar_ts": prices[-1][0],
            "spot": float(prices[-1][4]),
            "range": [d["rangeLow"], d["rangeHigh"]],
            "levels": sorted((y[p], v) for t, p, v in d["liq"] if t == t_now)}


def r_longshort(d: Any) -> dict:
    rows = d[0].get("list", []) if isinstance(d, list) and d else []
    return {"venues": {r["exchangeName"]: {
        "long": r.get("longRate"), "short": r.get("shortRate"),
        "long_usd": r.get("longVolUsd"), "short_usd": r.get("shortVolUsd")}
        for r in rows}}


def r_liqtoday(d: Any) -> dict:
    """Realised liquidations. Keep the aggregates, drop the UI payload.

    The full response is ~5.7KB of exchange lists, tickers and 'similar day'
    comparisons -- 58% of a row, all of it replayable and none of it used.
    """
    if not isinstance(d, dict):
        return {"unexpected_type": type(d).__name__}
    keep = ("longLiquidationUsd", "shortLiquidationUsd", "liquidationUsd",
            "longLiquidationRate", "shortLiquidationRate",
            "peakLiquidationHour", "liquidationTraders", "avg7dRate", "30dMaxRate")
    return {k: d[k] for k in keep if k in d}


# --- registry ----------------------------------------------------------------

STREAMS: dict[str, Stream] = {
    s.name: s for s in [
        Stream("funding", f"{API}/fundingRate/v2/history/chart?symbol=BTC&type=U&interval=h8",
               r_funding, replayable=True, note="180d of 8h funding across 14 venues"),
        Stream("basis", f"{API}/basis/v2/chart?symbol=BTC&exName=Binance&interval=h8",
               r_basis, replayable=True, note="perp + 2 quarterlies; ts offset one bar"),
        # heatmap_* streams are appended below, one per named window.
        Stream("longshort", f"{API}/futures/longShortRate?symbol=BTC&timeType=1",
               r_longshort, replayable=True, note="measured positioning, per venue"),
        Stream("liqtoday", f"{API}/futures/liquidation/today?symbol=BTC",
               r_liqtoday, replayable=True, note="realised liquidations, to score the map"),
    ]
}

# One stream per heatmap window. Each window's live book decays independently, so
# each is its own irreplaceable series -- a wider window is not a superset of a
# narrower one at a later date, it is a different resolution of a moment that is
# already gone. All seven cost 23KB/run combined (~35MB/year at 4 runs/day), so
# there is no cadence machinery here: collect them all, every run.
for _w, (_q, _gated) in HEATMAP_WINDOWS.items():
    STREAMS[f"heatmap_{_w}"] = Stream(
        f"heatmap_{_w}",
        f"{API}/index/aggregate/liqHeatMap?merge=true&symbol=BTC&{_q}",
        r_heatmap, replayable=False, needs_token=True, needs_session=_gated,
        note=f"NOT REPLAYABLE - {_w} window" + (" (session-gated)" if _gated else ""))
del _w, _q, _gated


def gated_streams() -> set[str]:
    return {n for n, s in STREAMS.items() if s.needs_session}
