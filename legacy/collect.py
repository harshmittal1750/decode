#!/usr/bin/env python3
"""Append one row per run to data/collector.jsonl from 5 CoinGlass streams.

Retention follows REPLAYABILITY, not a uniform disk budget:
  funding / basis / longShort  -> every call returns 180d of history, so we keep
      only the current slice; a future bug can be fixed by re-fetching.
  liq heatmap -> NOT replayable. Swept levels decay out of the payload and are
      archived nowhere. We store the full live book (last time column, ~250
      levels) forever. That is the irreplaceable part; the other 15k cells are
      older columns we can always recompute from nothing, so they are dropped.
      ~8KB/run => ~12MB/year at 4 runs/day.

Re-runs append rather than overwrite: the heatmap decays in real time, so two
pulls of the same bar are two different measurements. fetch_ts is data.

Partial failure writes what worked and exits 0 -- the 5 streams are independent,
so one 500 must not cost the other four. Failures land in the row's "errors".
"""
import json, os, sys, time, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cg, cgtoken

def _q(v):
    import urllib.parse
    return urllib.parse.quote(v, safe='')


OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'collector.jsonl')
API = 'https://capi.coinglass.com/api'


def r_funding(d):
    """Current funding per venue + spot. Replayable -> current slice only."""
    i = -1
    return {'ts': d['dateList'][i], 'spot': d['priceList'][i],
            'rates': {ex: v[i] for ex, v in d['dataMap'].items()}}


def r_basis(d):
    """Perp + quarterly prices. Replayable -> current slice only.
    NB: dateList here is offset one bar from funding's; see pressure.align()."""
    return {'ts': d['dateList'][-1],
            'px': {s['instrumentId']: s['priceList'][-1] for s in d['data']}}


def r_heatmap(d):
    """The live book: last time column only. NOT replayable -> keep in full."""
    t = max(c[0] for c in d['liq'])
    y = d['y']
    return {'spot': float(d['prices'][-1][4]), 'bar_ts': d['prices'][-1][0],
            'range': [d['rangeLow'], d['rangeHigh']],
            'levels': sorted((y[p], v) for tt, p, v in d['liq'] if tt == t)}


def r_longshort(d):
    rows = d[0]['list'] if isinstance(d, list) and d and 'list' in d[0] else []
    return {'venues': {r['exchangeName']: {'long': r.get('longRate'), 'short': r.get('shortRate'),
                                           'longUsd': r.get('longVolUsd'), 'shortUsd': r.get('shortVolUsd')}
                       for r in rows}}


def r_liqtoday(d):
    return d if isinstance(d, (dict, list)) else {'raw': str(d)[:500]}


STREAMS = {
    'funding':   (f'{API}/fundingRate/v2/history/chart?symbol=BTC&type=U&interval=h8', r_funding),
    'basis':     (f'{API}/basis/v2/chart?symbol=BTC&exName=Binance&interval=h8',       r_basis),
    # data= is a 30s-TOTP token (see cgtoken.py); regenerated per run, so this
    # stream runs unattended. Was: paste a fresh value from devtools every hour.
    'heatmap':   (f'{API}/index/aggregate/liqHeatMap?merge=true&symbol=BTC&interval=5&limit=288'
                  '&data={TOKEN}', r_heatmap),
    'longshort': (f'{API}/futures/longShortRate?symbol=BTC&timeType=1',                r_longshort),
    'liqtoday':  (f'{API}/futures/liquidation/today?symbol=BTC',                       r_liqtoday),
}


def collect(streams=STREAMS, obe=None, ua=None, fetch=None):
    # obe is a 180-day login cookie, and every one of these 5 endpoints was
    # verified to return identical data with it absent, garbage, or real.
    # Kept as an optional passthrough only; never required, never needed by cron.
    fetch = fetch or (lambda u: cg.fetch_curl(u, obe or os.environ.get('CG_OBE', ''),
                                              ua or os.environ.get('CG_UA', '')))
    row = {'fetch_ts': int(time.time() * 1000), 'errors': {}}
    for name, (url, reduce_) in streams.items():
        try:
            if '{TOKEN}' in url:                # TOTP expires in 30s: mint at call time
                url = url.replace('{TOKEN}', _q(cgtoken.make_token()))
            d = fetch(url)
            if isinstance(d, dict) and d.get('success') is False:
                raise RuntimeError(f"api {d.get('code')}: {d.get('msg')}")
            row[name] = reduce_(d)
        except Exception as e:                      # one bad stream must not sink the row
            msg = f'{type(e).__name__}: {e}'
            if name == 'heatmap' and '40001' in msg:
                msg += '  <- token rejected; CoinGlass may have rotated the bundle key (cgtoken.py)'
            row['errors'][name] = msg
    return row


def main():
    row = collect()
    got = [k for k in STREAMS if k in row]
    if not got:
        # every stream failed -> almost always an expired obe token, not 5 outages
        print(f"ALL {len(STREAMS)} STREAMS FAILED - check CG_OBE is current", file=sys.stderr)
        for k, v in row['errors'].items():
            print(f"  {k}: {v}", file=sys.stderr)
        return 1                                    # only hard-fail when nothing worked
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'a') as f:
        f.write(json.dumps(row, separators=(',', ':')) + '\n')
    print(f"ok {len(got)}/{len(STREAMS)}: {' '.join(got)}"
          + (f"  FAILED: {' '.join(row['errors'])}" if row['errors'] else ''))
    return 0


def demo():
    calls = []

    def fake(url):
        calls.append(url)
        if 'liqHeatMap' in url:
            return {'liq': [[0, 0, 5.0], [0, 1, 6.0], [1, 0, 9.0], [1, 1, 1.0]],
                    'y': [100.0, 200.0], 'prices': [[7, 0, 0, 0, '150']],
                    'rangeLow': 90, 'rangeHigh': 210}
        if 'longShortRate' in url:
            raise RuntimeError('boom 500')          # one stream down
        return {'success': False, 'code': '40001', 'msg': 'bad param'}

    row = collect({k: v for k, v in STREAMS.items() if k in ('heatmap', 'longshort', 'liqtoday')},
                  fetch=fake)
    # the good stream survived its neighbours failing
    assert row['heatmap']['levels'] == [(100.0, 9.0), (200.0, 1.0)], row['heatmap']
    assert row['heatmap']['spot'] == 150.0
    # both failure shapes are captured, not raised
    assert 'longshort' in row['errors'] and 'boom 500' in row['errors']['longshort']
    assert 'liqtoday' in row['errors'] and '40001' in row['errors']['liqtoday']
    assert 'longshort' not in row and 'liqtoday' not in row
    # stale time columns are dropped; only the live book is kept
    assert all(lv in ((100.0, 9.0), (200.0, 1.0)) for lv in row['heatmap']['levels'])
    assert len(calls) == 3
    print('self-check ok')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--test']:
        demo()
    else:
        sys.exit(main())
