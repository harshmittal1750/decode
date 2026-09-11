"""Spot vs futures pressure, from the perp-spot basis.

Basis is the honest instrument, NOT funding. Funding is clamped per venue
(Binance caps at 0.01%), sampled at different intervals per venue (Hyperliquid
funds hourly, Binance 8h), and some venues repeat a stale value 59% of the time.
A cross-venue funding mean therefore measures exchange mechanics: per-venue
corr(return, dfunding) ranges from -0.395 to +0.249 on the same 180 days.

Reading:
    price up + basis expanding   -> futures leading (leverage bid)
    price up + basis compressing -> spot leading (cash buying, perps lag)

Guard: a "basis" whose sd approaches the return sd is not a basis, it is a
misaligned price change. sanity_check() refuses those -- this exact mistake
produced a corr(return_t+1, basis_t) of +0.998 during development.
"""
from __future__ import annotations

import statistics as st

from .. import fmt

MAX_PLAUSIBLE_BASIS_SD = 0.3     # real perp-spot basis is tenths of a %


class AlignmentError(Exception):
    """The basis series looks like a price change, i.e. the join is wrong."""


def corr(a, b) -> float:
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def sanity_check(basis: list[float]) -> None:
    if len(basis) < 3:
        return
    sd = st.stdev(basis)
    if sd > MAX_PLAUSIBLE_BASIS_SD:
        raise AlignmentError(
            f"basis sd={sd:.4f}% exceeds {MAX_PLAUSIBLE_BASIS_SD}% -- this is a price "
            "change, not a basis. The funding/basis join is misaligned; fix "
            "store.aligned() rather than trusting these numbers.")


QUADRANT = {("up", "B+"): "FUTURES-led UP  ", ("up", "B-"): "SPOT-led UP     ",
            ("dn", "B-"): "FUTURES-led DOWN", ("dn", "B+"): "SPOT-led DOWN   "}


def decompose(rows: list[dict]) -> dict:
    """rows come from store.aligned(). Returns quadrants, correlations, net attribution."""
    basis = [r["basis_pct"] for r in rows]
    spot = [r["spot"] for r in rows]
    sanity_check(basis)
    if len(rows) < 4:
        raise ValueError(f"need >=4 aligned rows, have {len(rows)}")

    ret = [(spot[i] / spot[i - 1] - 1) * 100 for i in range(1, len(spot))]
    dbasis = [basis[i] - basis[i - 1] for i in range(1, len(basis))]

    quads: dict[tuple, list[float]] = {}
    for r, d in zip(ret, dbasis):
        quads.setdefault(("up" if r > 0 else "dn", "B+" if d > 0 else "B-"), []).append(r)

    net_fut = sum(quads.get(("up", "B+"), [])) + sum(quads.get(("dn", "B-"), []))
    net_spot = sum(quads.get(("up", "B-"), [])) + sum(quads.get(("dn", "B+"), []))
    return {
        "n": len(ret),
        "basis_now": basis[-1],
        "basis_mean": st.mean(basis),
        "basis_z": ((basis[-1] - st.mean(basis)) / st.stdev(basis)) if len(basis) > 2 else 0.0,
        "corr_ret_dbasis": corr(ret, dbasis),
        "corr_next_basis": corr(ret[1:], basis[1:-1]) if len(ret) > 2 else 0.0,
        "quadrants": {QUADRANT[k]: {"n": len(v), "cum": sum(v), "avg": st.mean(v)}
                      for k, v in quads.items()},
        "net_futures_led": net_fut,
        "net_spot_led": net_spot,
    }


def report(rows: list[dict]) -> str:
    d = decompose(rows)
    lean = ("futures-led" if d["corr_ret_dbasis"] > 0.05
            else "spot-led" if d["corr_ret_dbasis"] < -0.05 else "neither clearly")

    state = fmt.kv([
        ("Bars analysed", f"{d['n']}  (8h each, ~{d['n'] / 3:.0f} days)"),
        ("Perp vs spot basis", f"{d['basis_now']:+.4f}%   "
                               f"(180d mean {d['basis_mean']:+.4f}%,  z = {d['basis_z']:+.2f})"),
        ("Market is", f"{lean}"),
    ], indent=2)

    corr = fmt.table(
        [("Measure", "<"), ("Value", ">"), ("Reads as", "<")],
        [["Return vs change in basis", f"{d['corr_ret_dbasis']:+.3f}",
          "above 0 = futures leading, below 0 = spot leading"],
         ["Next return vs basis", f"{d['corr_next_basis']:+.3f}",
          "forecasting power (near zero = none)"]],
        indent=2)

    order = ["FUTURES-led UP  ", "SPOT-led UP     ", "FUTURES-led DOWN", "SPOT-led DOWN   "]
    qrows = []
    for label in order:
        q = d["quadrants"].get(label)
        if not q:
            continue
        who, direction = label.split("-led")
        qrows.append([who.strip().title(), direction.strip().title(), str(q["n"]),
                      f"{q['avg']:+.3f}%", f"{q['cum']:+.1f}%"])
    quads = fmt.table(
        [("Driven by", "<"), ("Direction", "<"), ("Bars", ">"),
         ("Avg move", ">"), ("Total move", ">")], qrows, indent=2)

    net = fmt.table(
        [("Attribution", "<"), ("Net price move", ">")],
        [["Futures-led bars (leverage)", f"{d['net_futures_led']:+.1f}%"],
         ["Spot-led bars (cash)", f"{d['net_spot_led']:+.1f}%"]], indent=2)

    return "\n".join([
        fmt.heading("SPOT vs FUTURES PRESSURE"), "", state,
        "", "  How price and the futures premium move together:", "", corr,
        "", "  Every bar classified by what drove it:", "", quads,
        "", "  Which kind of flow actually produced the net move:", "", net,
        "",
        "  Basis = perp price minus spot index. Rising basis while price rises",
        "  means futures are leading; falling basis means spot is leading.",
        "  This explains what already happened. It does not forecast.",
    ])


# --- plain-language attribution ----------------------------------------------
#
# "Futures-led" alone does not say WHY. Two very different things look identical
# in the basis: someone opening leveraged longs, and shorts being force-bought
# out of their positions. Realised liquidations separate them.
#
# Resolution is DAILY, because CoinGlass only serves daily liquidation bars --
# it ignores the interval parameter entirely. So the 8h basis series is folded
# into days before joining.

FORCED_SHARE_HEAVY = 0.5     # a day is "liquidation-driven" above this share


def _day(ms: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.UTC).strftime("%Y-%m-%d")


def _daily(rows: list[dict]) -> dict[str, dict]:
    """Fold 8h aligned rows into calendar days: return, basis change, spot."""
    days: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        key = _day(r["ts"])
        days.setdefault(key, []).append(r)
    out = {}
    prev = None
    for key in sorted(days):
        bars = days[key]
        close, close_basis = bars[-1]["spot"], bars[-1]["basis_pct"]
        if prev is not None:
            # BOTH measured close-to-close. Taking the basis change intra-day
            # while the return is day-over-day compares two different intervals,
            # and collapses to zero entirely when a day holds one bar.
            out[key] = {"ret_pct": (close / prev[0] - 1) * 100,
                        "dbasis": close_basis - prev[1],
                        "spot": close}
        prev = (close, close_basis)
    return out


def attribute(rows: list[dict], liq_series: list) -> dict:
    """Split the move into: forced (liquidations) vs fresh positioning vs spot.

    For each day we know the price move, whether the futures premium expanded,
    and how much was actually liquidated. That is enough to say which of three
    things happened, in words a reader can check:

      price up   + basis up + mostly forced BUYING   -> short squeeze
      price up   + basis up + light liquidations     -> fresh leveraged buying
      price down + basis dn + mostly forced SELLING  -> long liquidation cascade
      basis moving against price                     -> spot-led (cash, not leverage)

    Days with no liquidation data are counted separately rather than being
    folded into "fresh" -- absent data is not evidence of light liquidations.
    """
    daily = _daily(rows)
    liq = {_day(ts): (buy or 0.0, sell or 0.0)
           for ts, buy, sell, _px in (liq_series or [])}

    buckets = {"squeeze": 0.0, "fresh_leverage": 0.0, "long_cascade": 0.0,
               "fresh_shorting": 0.0, "spot_led": 0.0}
    counts = dict.fromkeys(buckets, 0)
    forced = {"buy": 0.0, "sell": 0.0}
    no_liq_days = 0

    for key, d in daily.items():
        ret, dbasis = d["ret_pct"], d["dbasis"]
        if ret == 0:
            continue
        # Futures-led when the premium expands in the same direction as price.
        if (ret > 0) != (dbasis > 0):
            buckets["spot_led"] += ret
            counts["spot_led"] += 1
            continue
        if key not in liq:
            no_liq_days += 1          # cannot attribute; do not guess
            continue
        buy, sell = liq[key]
        forced["buy"] += buy
        forced["sell"] += sell
        total = buy + sell
        if ret > 0:
            share = (buy / total) if total else 0.0
            k = "squeeze" if share >= FORCED_SHARE_HEAVY else "fresh_leverage"
        else:
            share = (sell / total) if total else 0.0
            k = "long_cascade" if share >= FORCED_SHARE_HEAVY else "fresh_shorting"
        buckets[k] += ret
        counts[k] += 1

    net_fut = sum(v for k, v in buckets.items() if k != "spot_led")
    return {"days": len(daily), "days_unattributable": no_liq_days,
            "buckets": buckets, "counts": counts,
            "net_futures_led": net_fut, "net_spot_led": buckets["spot_led"],
            "forced_buy_usd": forced["buy"], "forced_sell_usd": forced["sell"],
            "verdict": _verdict(net_fut, buckets["spot_led"], buckets)}


def _verdict(net_fut: float, net_spot: float, b: dict) -> dict:
    """One sentence a non-specialist can act on, plus the driver behind it."""
    total = abs(net_fut) + abs(net_spot)
    fut_share = (abs(net_fut) / total * 100) if total else 0.0
    led = "futures" if abs(net_fut) > abs(net_spot) else "spot"
    drivers = {"squeeze": "shorts being forced to buy back",
               "fresh_leverage": "new leveraged buying",
               "long_cascade": "longs being forced to sell",
               "fresh_shorting": "new leveraged selling"}
    top = max((k for k in drivers), key=lambda k: abs(b.get(k, 0.0)))
    return {"led_by": led, "futures_share_pct": fut_share,
            "main_driver": top, "main_driver_label": drivers[top],
            "main_driver_move_pct": b.get(top, 0.0)}
