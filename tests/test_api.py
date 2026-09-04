"""API routes are thin JSON wrappers over store.py/analysis -- one check per
route that it reaches the right data, not a re-test of the underlying logic
(that's covered in test_store.py / test_analysis.py).
"""
import pytest
from fastapi.testclient import TestClient

from decode import config, pipeline, store
from decode.client import Fetched
from decode.streams import Stream, r_heatmap

HEATMAP_PAYLOAD = {
    "liq": [[0, 0, 5.0], [0, 1, 6.0]],
    "y": [100.0, 200.0], "prices": [[7, 0, 0, 0, "150"]],
    "rangeLow": 90, "rangeHigh": 210,
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    from decode import api
    return TestClient(api.app)


def _seed_heatmap(db_path):
    conn = store.connect(db_path)
    s = {"heatmap": Stream("heatmap", "u", r_heatmap, replayable=False)}
    fetch = lambda url, **kw: Fetched(
        url=url, payload=HEATMAP_PAYLOAD, raw=b"{}", status=200, headers={},
        elapsed_ms=1, attempts=1)
    pipeline.run_once(conn, s, fetch=fetch)
    conn.close()


def test_status_empty_archive(client):
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json()["runs"] == 0


def test_heatmap_404_before_any_collect(client):
    r = client.get("/heatmap")
    assert r.status_code == 404


def test_heatmap_and_liq_after_seeding(client):
    _seed_heatmap(config.DB_PATH)

    r = client.get("/heatmap")
    assert r.status_code == 200
    assert r.json()["spot"] == 150.0

    r = client.get("/liq", params={"targets": "100,200"})
    assert r.status_code == 200
    body = r.json()
    assert body["spot"] == 150.0
    assert {t["target"] for t in body["targets"]} == {100.0, 200.0}
    # spot=150, target=100 is a downward move -> longs standing below get hit
    below = next(t for t in body["targets"] if t["target"] == 100.0)
    assert below["side"] == "long"


def test_pressure_needs_four_rows(client):
    r = client.get("/pressure")
    assert r.status_code == 400


def test_unknown_stream_404(client):
    r = client.get("/nope")
    assert r.status_code == 404
