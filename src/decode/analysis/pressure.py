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
    out = [f"n={d['n']} bars   basis now {d['basis_now']:+.4f}%  "
           f"mean {d['basis_mean']:+.4f}%  z={d['basis_z']:+.2f}",
           f"corr(return, dbasis)  = {d['corr_ret_dbasis']:+.3f}   >0 futures-led, <0 spot-led",
           f"corr(return+1, basis) = {d['corr_next_basis']:+.3f}   predictive", ""]
    for label, q in sorted(d["quadrants"].items()):
        out.append(f"  {label} n={q['n']:3}  avg {q['avg']:+.3f}%  cumulative {q['cum']:+7.1f}%")
    out += ["", f"  NET futures-led {d['net_futures_led']:+.1f}%   "
                f"NET spot-led {d['net_spot_led']:+.1f}%"]
    return "\n".join(out)
