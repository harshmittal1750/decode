"""Pipeline: independence of streams, and that every failure surface is captured."""
import json

import pytest

from decode import pipeline, store
from decode.client import Fetched
from decode.streams import Stream, r_heatmap


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


HEATMAP_PAYLOAD = {
    "liq": [[0, 0, 5.0], [0, 1, 6.0], [1, 0, 9.0], [1, 1, 1.0]],
    "y": [100.0, 200.0], "prices": [[7, 0, 0, 0, "150"]],
    "rangeLow": 90, "rangeHigh": 210,
}


def fetched(payload, url="http://x"):
    return Fetched(url=url, payload=payload, raw=json.dumps(payload).encode(),
                   status=200, headers={}, elapsed_ms=5, attempts=1)


def test_one_stream_failing_does_not_sink_the_others(conn):
    streams = {
        "good": Stream("good", "u1", r_heatmap, replayable=False),
        "netfail": Stream("netfail", "u2", r_heatmap, replayable=True),
        "reducefail": Stream("reducefail", "u3", lambda d: 1 / 0, replayable=True),
    }

    def fake_fetch(url, **kw):
        if url == "u2":
            raise ConnectionError("boom")
        return fetched(HEATMAP_PAYLOAD, url)

    res = pipeline.run_once(conn, streams, fetch=fake_fetch)
    assert res.ok == ["good"]
    assert set(res.failed) == {"netfail", "reducefail"}
    # the good row landed
    assert store.latest(conn, "good")["spot"] == 150.0
    # and both failures are recorded with their stage
    errs = {e["stream"]: e["message"] for e in store.recent_errors(conn)}
    assert "[fetch] ConnectionError" in errs["netfail"]
    assert "[reduce] ZeroDivisionError" in errs["reducefail"]


def test_reduce_failure_still_archives_raw(conn):
    """The whole point of raw: fix the reducer, replay the bytes that broke it."""
    bad = {"boom": Stream("boom", "u", lambda d: d["missing_key"], replayable=True)}
    pipeline.run_once(conn, bad, fetch=lambda url, **kw: fetched(HEATMAP_PAYLOAD, url))

    assert store.latest(conn, "boom") is None          # nothing processed
    body = store.raw_body(conn, 1, "boom")             # but raw survived
    assert json.loads(body) == HEATMAP_PAYLOAD


def test_replay_reruns_reducer_over_archived_raw(conn, monkeypatch):
    s = {"heatmap": Stream("heatmap", "u", r_heatmap, replayable=False)}
    monkeypatch.setitem(pipeline.STREAMS, "heatmap", s["heatmap"])
    pipeline.run_once(conn, s, fetch=lambda url, **kw: fetched(HEATMAP_PAYLOAD, url))
    assert pipeline.replay(conn, 1, "heatmap") == r_heatmap(HEATMAP_PAYLOAD)


def test_heatmap_reducer_keeps_only_the_live_column():
    """Stale time columns must be dropped or the same money counts once per bar."""
    out = r_heatmap(HEATMAP_PAYLOAD)
    assert out["levels"] == [(100.0, 9.0), (200.0, 1.0)]   # t=1 only, not t=0
    assert out["spot"] == 150.0


def test_run_is_recorded_even_when_everything_fails(conn):
    s = {"a": Stream("a", "u", r_heatmap, replayable=True)}

    def boom(url, **kw):
        raise ConnectionError("down")

    res = pipeline.run_once(conn, s, fetch=boom)
    assert res.ok == [] and res.failed
    assert store.stats(conn)["runs"] == 1
