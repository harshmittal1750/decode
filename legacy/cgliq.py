#!/usr/bin/env python3
"""How much liquidation sits between spot and a target price, on a CoinGlass heatmap.

Grid semantics (verified against the payload):
  liq  = [time_idx, price_idx, usd] sparse cells; values are STANDING liquidity
         carried forward in time and decaying when price sweeps the level.
  => only the LAST time column is the live book. A level absent there is cleared.
  => standing level below spot  == longs  liquidate on the way down
     standing level above spot  == shorts liquidate on the way up
"""
import json, os, sys, time

def book(d):
    """(spot, {price_level: standing_usd}) from the newest time column."""
    t_now = max(c[0] for c in d['liq'])
    y = d['y']
    return float(d['prices'][-1][4]), {y[p]: v for t, p, v in d['liq'] if t == t_now}

def between(spot, levels, target):
    """Liquidations triggered by a move from spot to target."""
    lo, hi = sorted((spot, target))
    hit = {p: v for p, v in levels.items() if lo <= p <= hi}
    return {
        'side': 'short' if target > spot else 'long',
        'total': sum(hit.values()),
        'levels': sorted(hit.items()),
    }

def at(levels, target):
    """Standing liquidity in the single bucket nearest to target."""
    p = min(levels, key=lambda p: abs(p - target))
    return p, levels[p]

def report(d, targets):
    spot, levels = book(d)
    lo, hi = min(levels), max(levels)
    print(f"spot ${spot:,.0f}   heatmap covers ${lo:,.0f}–${hi:,.0f}   "
          f"bucket ~${(hi-lo)/(len(levels)-1):,.0f}\n")
    for tgt in targets:
        r = between(spot, levels, tgt)
        bp, bv = at(levels, tgt)
        warn = "  [OUTSIDE HEATMAP RANGE — undercounted]" if not lo <= tgt <= hi else ""
        print(f"${tgt:,.0f}  ({r['side']}s){warn}")
        print(f"  cumulative to here : ${r['total']/1e9:,.3f}B  across {len(r['levels'])} levels")
        print(f"  at this level alone: ${bv/1e6:,.1f}M  (bucket ${bp:,.0f})")
        top = sorted(r['levels'], key=lambda kv: -kv[1])[:5]
        print("  biggest clusters   : " + ", ".join(f"${p:,.0f}=${v/1e6:.0f}M" for p, v in top))
        print()

def demo():
    d = {'y': [100, 200, 300, 400], 'prices': [[0, 0, 0, 0, '250']],
         'liq': [[0, 0, 5.0], [0, 3, 7.0],           # stale column, must be ignored
                 [1, 0, 9.0], [1, 1, 1.0], [1, 3, 4.0]]}
    spot, lv = book(d)
    assert spot == 250 and lv == {100: 9.0, 200: 1.0, 400: 4.0}, lv
    down = between(spot, lv, 150)
    assert down['side'] == 'long' and down['total'] == 1.0, down    # only 200 is in [150,250]
    assert between(spot, lv, 100)['total'] == 10.0                  # 100 + 200
    up = between(spot, lv, 400)
    assert up['side'] == 'short' and up['total'] == 4.0, up         # 400 only
    assert at(lv, 210) == (200, 1.0)
    print("self-check ok")

if __name__ == "__main__":
    if sys.argv[1:2] == ["--test"]:
        demo()
        sys.exit()
    url = next((a for a in sys.argv[1:] if a.startswith('http')), '')
    src = next((a for a in sys.argv[1:] if a.endswith('.json')), 'heatmap.json')
    targets = [float(a) for a in sys.argv[1:] if a.replace('.', '').isdigit()] or [85000, 60000]
    if url:
        import cg
        d = cg.fetch_curl(url, os.environ.get('CG_OBE', ''), os.environ.get('CG_UA', ''))
        if 'liq' not in d:
            sys.exit(f"fetch failed: {d}  (set CG_OBE to the obe header from your curl)")
        src = f"heatmap_{time.strftime('%Y%m%d_%H%M')}.json"
        json.dump(d, open(src, 'w'))
        print(f"saved {src}\n")
    report(json.load(open(src)), targets)
