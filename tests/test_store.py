"""Store: atomicity, retention policy, and the alignment join."""
import pytest

from decode import store


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_series_and_latest_round_trip(conn):
    rid = store.start_run(conn)
    store.save_processed(conn, rid, "funding", {"ts": 1, "spot": 100})
    rid2 = store.start_run(conn)
    store.save_processed(conn, rid2, "funding", {"ts": 2, "spot": 200})
    assert [r["spot"] for r in store.series(conn, "funding")] == [100, 200]
    assert store.latest(conn, "funding")["spot"] == 200
    assert store.latest(conn, "nothing") is None


def test_sweep_never_touches_processed(conn):
    """The heatmap live book is unrecoverable; retention must only hit raw."""
    rid = store.start_run(conn)
    store.save_processed(conn, rid, "heatmap", {"levels": [[1, 2]]})
    conn.execute("INSERT INTO raw (run_id,stream,fetched_at,url,body_gz) VALUES (?,?,?,?,?)",
                 (rid, "heatmap", 0, "u", b"x"))          # fetched_at=0 -> ancient
    assert store.sweep_raw(conn, days=1) == 1
    assert store.stats(conn)["raw_rows"] == 0
    assert store.latest(conn, "heatmap") is not None      # survives

def test_rollback_leaves_no_partial_row(conn):
    rid = store.start_run(conn)
    with pytest.raises(RuntimeError):
        with store.transaction(conn):
            store.save_processed(conn, rid, "funding", {"ts": 1})
            raise RuntimeError("mid-write failure")
    assert store.latest(conn, "funding") is None


H = 8 * 3600 * 1000


def _walk(n=12, start=70000.0):
    """Deterministic price path with BTC-like 1.2%/bar volatility."""
    out, x = [start], 12345
    for _ in range(n - 1):
        x = (1103515245 * x + 12345) % (1 << 31)
        out.append(out[-1] * (1 + 0.012 * ((x / (1 << 31)) - 0.5) * 2))
    return out


def _seed(conn, shift=1, premium=1.0003, n=12):
    """perp at bar t priced off the spot `shift` bars later -> that is the truth."""
    spot = _walk(n)
    ts = [i * H for i in range(n)]
    rid = store.start_run(conn)
    store.save_processed(conn, rid, "funding",
                         {"ts": ts[-1], "spot": spot[-1], "rates": {"X": 0.01},
                          "series": list(zip(ts, spot))})
    perp = [(ts[i], spot[i + shift] * premium) for i in range(n - shift)]
    store.save_processed(conn, rid, "basis",
                         {"ts": perp[-1][0], "px": {"BTCUSD_PERP": perp[-1][1]},
                          "series": perp})
    return spot, ts


def test_aligned_detects_the_offset(conn):
    """basis at bar t pairs with the spot index stamped one bar LATER."""
    _seed(conn, shift=1)
    rows = store.aligned(conn)
    assert rows and all(r["shift"] == 1 for r in rows)
    assert all(r["basis_pct"] == pytest.approx(0.03, abs=1e-6) for r in rows)


def test_aligned_refuses_an_unjoinable_pair(conn):
    """No shift yields a plausible basis -> return nothing rather than an artifact."""
    spot = _walk(12)
    ts = [i * H for i in range(12)]
    rid = store.start_run(conn)
    store.save_processed(conn, rid, "funding",
                         {"ts": ts[-1], "spot": spot[-1], "rates": {},
                          "series": list(zip(ts, spot))})
    # perp offset by 6 bars: every shift is a multi-bar price change, not a basis
    store.save_processed(conn, rid, "basis",
                         {"ts": ts[0], "px": {},
                          "series": [(ts[i], spot[i + 6]) for i in range(6)]})
    assert store.aligned(conn) == []


def test_aligned_needs_both_streams(conn):
    rid = store.start_run(conn)
    store.save_processed(conn, rid, "basis", {"ts": 999, "px": {}, "series": []})
    assert store.aligned(conn) == []


def test_raw_body_round_trips_gzip(conn):
    import gzip, json
    rid = store.start_run(conn)
    payload = {"hello": "world"}
    conn.execute("INSERT INTO raw (run_id,stream,fetched_at,url,body_gz) VALUES (?,?,?,?,?)",
                 (rid, "s", store.now_ms(), "u", gzip.compress(json.dumps(payload).encode())))
    assert json.loads(store.raw_body(conn, rid, "s")) == payload
    assert store.raw_body(conn, 999, "s") is None
