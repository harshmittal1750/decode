"""API smoke tests -- every route answers, and shapes match what the UI expects."""
import pytest
from fastapi.testclient import TestClient

from decode import api, config, store
from decode.streams import r_heatmap

# Two clusters with a gap across spot=150, so zones land cleanly on each side.
# A book of evenly-spaced levels would merge into ONE zone straddling spot --
# correct behaviour, but it exercises nothing.
BOOK = {"liq": [[1, 0, 9e6], [1, 1, 6e6],          # longs at 100, 110
                [1, 3, 4e6], [1, 4, 3e6],          # shorts at 300, 310
                [0, 0, 5e6]],                       # stale column, must be ignored
        "y": [100.0, 110.0, 200.0, 300.0, 310.0],
        "prices": [[7, 0, 0, 0, "150"]],
        "rangeLow": 90, "rangeHigh": 320}


@pytest.fixture
def client(tmp_path, monkeypatch):
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    conn = store.connect(dbp)
    rid = store.start_run(conn)
    for w in ["24h", "3d", "1w", "2w", "1m", "3m", "6m"]:
        store.save_processed(conn, rid, f"heatmap_{w}", r_heatmap(BOOK))
    store.save_processed(conn, rid, "funding",
                         {"ts": 0, "spot": 150.0, "rates": {"Binance": 0.01}, "series": []})
    store.save_processed(conn, rid, "longshort",
                         {"venues": {"Binance": {"long": 48.0, "short": 52.0}}})
    store.finish_run(conn, rid, 9, 0)
    conn.close()
    return TestClient(api.app)


def test_windows_reports_gating(client):
    rows = client.get("/windows").json()
    by = {r["window"]: r for r in rows}
    assert by["24h"]["gated"] is False and by["6m"]["gated"] is True


def test_status_marks_session_streams(client):
    per = client.get("/status").json()["per_stream"]
    assert per["heatmap_6m"]["needs_session"] is True
    assert per["heatmap_24h"]["needs_session"] is False


def test_heatmap_returns_sides_and_distance(client):
    d = client.get("/heatmap/24h").json()
    assert d["spot"] == 150.0
    sides = {lv["price"]: lv["side"] for lv in d["levels"]}
    assert sides[100.0] == "long" and sides[300.0] == "short"


def test_walls_gives_both_ladders_and_forcing(client):
    d = client.get("/walls/6m").json()
    assert d["short"]["forces"] == "buying" and d["long"]["forces"] == "selling"
    assert d["short"]["total_usd"] > 0


def test_liq_targets_flags_outside_range(client):
    d = client.get("/liq/24h", params={"targets": "250,9999"}).json()
    assert d["targets"][0]["outside_range"] is False
    assert d["targets"][1]["outside_range"] is True


def test_grid_includes_empty_buckets(client):
    d = client.get("/levels/grid", params={"step": 50}).json()
    assert any(r["total"] == 0 for r in d["rows"]), "empty buckets must be present"
    assert set(d["rows"][0]["cells"]) == set(d["windows"])


def test_levels_summary_has_a_row_per_window(client):
    rows = client.get("/levels/summary").json()
    assert len(rows) == 7 and rows[0]["short_core"]["dist_pct"] > 0


def test_unknown_window_is_400_not_500(client):
    assert client.get("/heatmap/nope").status_code == 400


def test_missing_data_is_404(client):
    assert client.get("/series/nothing").status_code == 404


def test_pressure_409s_without_enough_rows(client):
    assert client.get("/pressure").status_code == 409
