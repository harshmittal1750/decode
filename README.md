# decode

Decrypts CoinGlass's API, archives 14 BTC derivatives streams, and analyses
spot-vs-futures pressure and liquidation fuel.

```bash
uv run decode collect      # fetch everything into the archive
uv run decode status       # what is in there
uv run decode pressure     # spot vs futures decomposition
uv run decode liq 85000 60000
```

## Running the backend

```bash
uv sync                          # ingestion + API deps
uv sync --group browser          # + Playwright, only needed for `login`

uv run decode login               # one-time-per-expiry: capture a fresh
                                   # obe session via a real browser login
uv run decode collect --sweep     # fetch all streams; picks up the
                                   # captured session automatically
uv run decode serve --port 8000   # read-only JSON API over the archive
```

`decode login` opens a real, visible Chromium window. Log in yourself (email/
password or Google) — the password never touches this code, only the
resulting session value does. It's saved to `data/session.json` (gitignored)
and every `decode collect` after that reads it automatically; pass `--obe`
explicitly only to override it. When a stream fails with an API-level
rejection, `collect` prints `try: decode login` — that's your cue the session
went stale.

`decode serve` exposes `/status`, `/runs`, `/windows`, `/errors/recent`,
`/heatmap/{window}`, `/liq/{window}`, `/walls/{window}`, `/zones/{window}`,
`/levels/summary`, `/levels/grid`, `/pressure`, `/funding`, `/longshort`,
`/liqtoday`, `/series/{stream}` as JSON, reusing the same
store/analysis functions the CLI does — no separate logic to keep in sync.
The `frontend/` directory is a Next.js dashboard that consumes this API
(`npm run dev`, pointed at it via `NEXT_PUBLIC_API_URL`).

## Why this exists

CoinGlass serves **current state only**. Swept liquidation levels decay out of
the payload and are archived nowhere — you cannot ask the API what the heatmap
looked like last month. That history only exists if you create it, which is what
`collect` does.

## Layout

```
src/decode/
  config.py      paths, retention, tunables
  crypto.py      response decryption (webpack module 12471)
  token.py       the data= query token   (webpack module 94126)
  client.py      HTTP + retries + transparent decrypt
  store.py       SQLite; the ONLY module that touches the database
  streams.py     endpoint definitions and their reducers
  pipeline.py    fetch -> raw -> reduce -> processed, errors captured
  session.py     the persisted obe session (data/session.json)
  login.py       browser capture of a fresh obe (needs --group browser)
  api.py         read-only JSON API, `decode serve`
  fmt.py         shared money/percent/table formatting (CLI and UI agree)
  cli.py         command line
  analysis/
    liq.py       liquidation fuel between spot and a target price
    pressure.py  spot vs futures, from the perp-spot basis
```

## What gets collected

Every `decode collect` fetches all 14 streams. `decode status` lists them with
row counts and which need the login session.

| Stream | What it holds | Replayable? | Session? |
|---|---|---|---|
| `funding` | 8h funding across 14 venues + spot index, 30d tail | ✅ | — |
| `basis` | perp + 2 quarterly futures prices, 30d tail | ✅ | — |
| `longshort` | measured long/short split per venue | ✅ | — |
| `liqtoday` | realised liquidations, rolling 24h aggregate | ✅ | — |
| `liqhist` | **daily** realised liquidations, 120d of forced buys/sells | ✅ | — |
| `heatmap_12h` … `heatmap_3d` | live liquidation book, 4 short windows | ❌ | — |
| `heatmap_1w` … `heatmap_6m` | live liquidation book, 5 long windows | ❌ | ✅ |

### `liqhist` — what was actually forced

`liqtoday` is a single rolling 24h number; `liqhist` is the per-day series, and
it is what makes attribution possible:

```
buyVolUsd   shorts liquidated  ->  forced BUYING   (lifts price)
sellVolUsd  longs  liquidated  ->  forced SELLING  (drops price)
```

Basis alone cannot tell a short squeeze from fresh leveraged buying — both
expand the perp premium identically. Pairing the basis move with realised
liquidations separates them, which is what `decode pressure` and the dashboard's
"What is moving the price?" panel report:

| Day looks like | Called |
|---|---|
| price up, basis up, >50% forced buying | short squeeze |
| price up, basis up, light liquidations | fresh leveraged buying |
| price down, basis down, >50% forced selling | long liquidation cascade |
| basis moving against price | spot-led |

**Attribution is daily and cannot be finer.** The endpoint ignores `interval`
entirely — `h1`, `h4`, `h8`, `d1` and the numeric forms all return the same 180
daily rows (verified). The 8h basis series is folded into days before joining,
with return and basis change both measured close-to-close; measuring one
intra-day and the other day-over-day compares different intervals and collapses
to zero on single-bar days.

Days with no liquidation data are counted in `days_unattributable` and left out
rather than defaulted to "fresh" — absent data is not evidence of light
liquidations.

## Data states

Every run records four states, so a failure months from now is diagnosable:

| Table | Holds | Lifetime |
|---|---|---|
| `runs` | one row per run, timing, outcome | forever |
| `raw` | decrypted payload, gzipped, pre-reduction | 14 days |
| `processed` | the reduced rows analyses read | **forever** |
| `errors` | every failure with stage + traceback | forever |

```bash
uv run decode errors -t              # recent failures with tracebacks
uv run decode raw 21 funding         # exactly what the API returned
uv run decode replay 21 heatmap      # re-run a reducer over archived raw
```

`replay` is the payoff for storing raw: fix a reducer, run it against the bytes
that broke it, and see what it *would* have produced.

### Retention follows replayability, not a disk budget

`funding`, `basis`, `longshort`, `liqtoday` and `liqhist` return their own
history on every call, so their raw blobs are only a debugging window and expire
after 14 days.

The **heatmap cannot be replayed**. Its processed rows are kept forever and
`sweep_raw()` is written so it can only ever touch the `raw` table.

## Two traps this codebase is built around

**1. The funding/basis join.** CoinGlass stamps these feeds one bar apart.
Joining them naively makes "basis" equal the *next* bar's return and produces a
correlation of **+0.998** — a spectacular-looking result that is pure artifact.
`store.aligned()` tries each shift, keeps the one whose basis has a plausible
magnitude (~0.07%, versus ~1.2% for a price change), and returns **nothing** if
no shift qualifies. A wrong join here fails silently; it returns numbers, just
false ones.

**2. Funding is not comparable across venues.** Intervals differ (Hyperliquid
funds hourly, Binance 8h), caps differ, and CoinEx repeats a stale value 59% of
the time. Per-venue `corr(return, Δfunding)` ranges from −0.395 to +0.249 over
the same 180 days. Averaging them measures exchange mechanics, not the market —
which is why `basis`, not funding, drives the pressure analysis.

## Scheduling

```bash
(crontab -l 2>/dev/null; echo "0 */6 * * * cd $PWD && $(which uv) run decode collect --sweep >> data/decode.log 2>&1") | crontab -
```

No credentials needed for the nine open streams — the `obe` session cookie is
optional there; every one was verified to return identical data with it
absent, garbage, or real. It does matter for extended-window heatmap queries
(interval/limit beyond the defaults), which 40000 without a valid session —
see "Running the backend" above for `decode login`.

Deploying `serve` to a machine without a display? The interactive `login`
step can only run somewhere with a real browser window. Run it locally, then
copy `data/session.json` to the remote host — `collect` there reads it the
same way.

The heatmap's `data=` token is a 30-second TOTP minted per request by
`token.py`. Both its constants ship in plaintext to every visitor, so this is
obfuscation, not a secret. CoinGlass can rotate them in any deploy —
`tests/test_token.py` reproduces three real captured tokens byte-for-byte, so
that failure is loud rather than silent.

**Clock skew matters.** The TOTP has a 30s step, so a box whose clock drifts
(a Pi without NTP) has every heatmap fetch rejected with `40001` and no other
symptom. `token.check_clock()` compares against the server's `Date` header each
run and logs a warning past 25s.

## Tests

```bash
uv run pytest
```

71 tests. Several pin bugs that actually occurred during development rather than
hypothetical ones:

- the +0.998 basis misalignment (a lookahead artifact, not a signal)
- counting liquidation levels a move never actually reaches
- window geometry inflating the long/short skew by 1.7×
- a gap-merge reporting an 8%-wide "zone" that was really a 1.5% wall plus tail
- attribution measuring the return close-to-close but the basis intra-day
- days with no liquidation data being silently classified as "fresh"

## Not done yet

- `data/decode.db` is on one disk with no backup. The processed table is
  unrecoverable; copy it somewhere.
- `data/decode.log` has no rotation.
- The Sep/Dec quarterly term structure annualises inverted (near-dated above
  far-dated), which suggests those contracts need their own alignment check.
  Unverified, so nothing uses it.
