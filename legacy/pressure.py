#!/usr/bin/env python3
"""Spot vs futures pressure on BTC, from CoinGlass basis + funding + liq heatmap.

Basis (perp price - spot index) is the honest instrument here, NOT funding:
funding is clamped per-venue (Binance caps at 0.01%), uses different intervals
per exchange (Hyperliquid 1h vs Binance 8h), and some venues repeat stale values
59% of the time -- so a cross-venue funding mean measures venue mechanics.

Read: price up + basis expanding = futures leading (leverage bid).
      price up + basis compressing = spot leading (cash buying, perps lag).

CAUTION: CoinGlass timestamps the funding priceList and the basis priceList with
different conventions (off by one bar). Misaligning them turns "basis" into next
bar's return and manufactures a corr of +0.998. align() detects the shift and
asserts the result is a plausible basis magnitude.
"""
import json, statistics as st, sys

H8 = 8 * 3600 * 1000
MAX_PLAUSIBLE_BASIS_SD = 0.3   # perp-spot basis is tenths of a %; a price change is ~1.2%

def corr(a, b):
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / ((sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** .5)

def align(fund, bas, bar_ms=H8):
    """[(ts, perp, spot)] on the shift that yields a plausible basis, not a price change."""
    smap = dict(zip(fund['dateList'], fund['priceList']))
    perp = {s['instrumentId']: s['priceList'] for s in bas['data']}
    pl = next(v for k, v in perp.items() if k.endswith('PERP'))
    best = None
    for sh in (-2, -1, 0, 1, 2):
        rows = [(d, p, smap[d + sh * bar_ms]) for d, p in zip(bas['dateList'], pl)
                if p and (d + sh * bar_ms) in smap]
        if len(rows) < 100:
            continue
        sd = st.stdev([(p / s - 1) * 100 for _, p, s in rows])
        if best is None or sd < best[0]:
            best = (sd, sh, rows)
    sd, sh, rows = best
    assert sd < MAX_PLAUSIBLE_BASIS_SD, (
        f"no alignment gives a plausible basis (best sd={sd:.3f}% at shift {sh:+d}). "
        "Series are mislabelled -- results would be a lookahead artifact.")
    return rows, sh

def decompose(rows):
    basis = [(p / s - 1) * 100 for _, p, s in rows]
    px = [s for _, _, s in rows]
    r = [(px[i] / px[i - 1] - 1) * 100 for i in range(1, len(px))]
    db = [basis[i] - basis[i - 1] for i in range(1, len(basis))]
    q = {}
    for ri, di in zip(r, db):
        q.setdefault(('up' if ri > 0 else 'dn', 'B+' if di > 0 else 'B-'), []).append(ri)
    return basis, r, db, q

NM = {('up', 'B+'): 'FUTURES-led UP  ', ('up', 'B-'): 'SPOT-led UP     ',
      ('dn', 'B-'): 'FUTURES-led DOWN', ('dn', 'B+'): 'SPOT-led DOWN   '}

def report(fund, bas, liq=None):
    rows, sh = align(fund, bas)
    basis, r, db, q = decompose(rows)
    z = (basis[-1] - st.mean(basis)) / st.stdev(basis)
    print(f"aligned on shift {sh:+d} bar, n={len(rows)}")
    print(f"basis (perp-spot): now {basis[-1]:+.4f}%  mean {st.mean(basis):+.4f}%  "
          f"sd {st.stdev(basis):.4f}%  z={z:+.2f}\n")
    print(f"corr(return_t,   Δbasis_t) = {corr(r, db):+.3f}   >0 futures-led, <0 spot-led")
    print(f"corr(return_t+1, basis_t)  = {corr(r[1:], basis[1:-1]):+.3f}   predictive")
    print(f"corr(return_t+1, Δbasis_t) = {corr(r[1:], db[:-1]):+.3f}   predictive")
    k = len(r) // 3
    print("  stability: " + "  ".join(
        f"{l}={corr(r[i*k:(i+1)*k], db[i*k:(i+1)*k]):+.3f}"
        for i, l in enumerate(['old', 'mid', 'new'])) + "\n")
    for key in [('up', 'B+'), ('up', 'B-'), ('dn', 'B-'), ('dn', 'B+')]:
        v = q.get(key, [])
        print(f"  {NM[key]} n={len(v):3} ({len(v)/len(r)*100:4.1f}%)  "
              f"avg {st.mean(v):+.3f}%  cumulative {sum(v):+7.1f}%")
    net_f = sum(q.get(('up','B+'),[])) + sum(q.get(('dn','B-'),[]))
    net_s = sum(q.get(('up','B-'),[])) + sum(q.get(('dn','B+'),[]))
    print(f"\n  NET from futures-led bars: {net_f:+.1f}%     from spot-led bars: {net_s:+.1f}%")
    if liq:
        sys.path.insert(0, '.')
        from cgliq import book
        spot, lv = book(liq)
        up = sum(v for p, v in lv.items() if p > spot)
        dn = sum(v for p, v in lv.items() if p < spot)
        print(f"\n  liq fuel: shorts above ${up/1e9:.2f}B  longs below ${dn/1e9:.2f}B  "
              f"skew {dn/up:.2f}x  (spot ${spot:,.0f})")

def demo():
    """Synthetic series with a KNOWN 1-bar offset; align() must find it and must
    refuse a feed where no shift yields a plausible basis."""
    n, base = 400, 70000.0
    # deterministic random walk with BTC-like 1.2%/bar vol, so a misaligned pairing
    # produces a visibly large "basis" and trips the guard
    spot, x = [base], 12345
    for _ in range(n - 1):
        x = (1103515245 * x + 12345) % (1 << 31)
        spot.append(spot[-1] * (1 + 0.012 * ((x / (1 << 31)) - .5) * 2))
    ts = [1772150400000 + i * H8 for i in range(n)]
    # perp[i] carries a +0.03% premium over spot[i+1]  -> true shift is +1
    perp = [spot[i + 1] * 1.0003 for i in range(n - 1)]
    fund = {'dateList': ts, 'priceList': spot}
    bas = {'dateList': ts[:n - 1], 'data': [{'instrumentId': 'X_PERP', 'priceList': perp}]}
    rows, sh = align(fund, bas)
    assert sh == 1, f"expected shift +1, got {sh}"
    b = [(p / s - 1) * 100 for _, p, s in rows]
    assert abs(st.mean(b) - 0.03) < 1e-6, st.mean(b)

    # perp offset by 40 bars: every shift yields a multi-bar price change, not a
    # basis. align() must raise rather than hand back the artifact.
    bad = {'dateList': [t + 40 * H8 for t in ts[:n - 1]],
           'data': [{'instrumentId': 'X_PERP', 'priceList': spot[:n - 1]}]}
    try:
        align(fund, bad)
        raise SystemExit("FAIL: accepted a misaligned feed")
    except AssertionError:
        pass
    print("self-check ok")

if __name__ == "__main__":
    if sys.argv[1:2] == ['--test']:
        demo()
    else:
        import os
        report(json.load(open('funding.json')), json.load(open('basis_hist.json')),
               json.load(open('liq5m.json')) if os.path.exists('liq5m.json') else None)
