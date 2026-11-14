from __future__ import annotations

import io

import pytest
from conftest import NOW, _id_of
from PIL import Image

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from review.app import create_app  # noqa: E402
from review.normalize import sample_id  # noqa: E402
from review.run import main, open_session  # noqa: E402


@pytest.fixture
def session(tmp_path, synth):
    store_root = tmp_path / "store"
    assert main(["init", "--store", str(store_root), "--sidecar", str(synth.sidecar_path)]) == 0
    s = open_session(store_root, synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path}, None, NOW)
    yield s
    s.close()


@pytest.fixture
def client(session):
    return TestClient(create_app(session, lambda: NOW))


def test_session_json_lists_candidates_in_manifest_order(client, session, synth):
    r = client.get("/api/session")
    assert r.status_code == 200
    j = r.json()
    assert j["session_id"] == "capture"
    assert set(j["stats"]) >= {"x_height", "median_half_gap", "flags"}
    assert [p["id"] for p in j["pages"]] == ["p0"]
    p = j["pages"][0]
    assert (p["width_px"], p["height_px"]) == (synth.page.width_px, synth.page.height_px)
    assert [r["id"] for r in p["regions"]] == [r.id for r in synth.page.regions]
    assert set(p["regions"][0]) == {"id", "bbox", "baseline_y", "expected_transcript"}
    cands = p["candidates"]
    assert [c["id"] for c in cands] == [c.id for c in synth.candidate_set.candidates]
    c0 = cands[0]
    assert set(c0) == {"id", "region_id", "confidence", "status", "context", "original_bbox", "decision"}
    assert all(c["decision"]["status"] == "pending" for c in cands)
    assert c0["decision"]["updated_at"] == NOW
    assert "source_word" in c0["context"] and set(c0["original_bbox"]) == {"x", "y", "w", "h"}


def test_page_image_is_png_of_page_size(client, synth):
    r = client.get("/api/page/p0/image.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(r.content)).size == (synth.page.width_px, synth.page.height_px)
    assert client.get("/api/page/nope/image.png").status_code == 404


def test_crop_is_png(client, synth):
    cid = synth.candidate_set.candidates[0].id
    r = client.get(f"/api/candidate/{cid}/crop.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    Image.open(io.BytesIO(r.content)).verify()
    assert client.get("/api/candidate/nope/crop.png").status_code == 404


def test_accept_writes_store_row(client, session):
    cid = _id_of(session.inputs, "c", region=0)
    r = client.post(f"/api/candidate/{cid}/accept", json={"quality": "good"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "accepted" and d["candidate_id"] == cid and d["updated_at"] == NOW
    assert session.store.get_sample(sample_id("capture", cid)) is not None
    assert client.post(f"/api/candidate/{cid}/accept").status_code == 200  # body optional


def test_quality_sets_quality(client, session):
    cid = _id_of(session.inputs, "a", region=0)
    r = client.post(f"/api/candidate/{cid}/quality", json={"quality": "marginal"})
    assert r.status_code == 200 and r.json()["quality"] == "marginal"
    assert client.post(f"/api/candidate/{cid}/quality", json={"quality": "awful"}).status_code == 422


def test_reject_and_reset(client, session):
    cid = _id_of(session.inputs, "t", region=0)
    assert client.post(f"/api/candidate/{cid}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/candidate/{cid}/reset").json()["status"] == "pending"


def test_bad_label_is_400(client, session):
    cid = _id_of(session.inputs, "a", region=0)
    r = client.post(f"/api/candidate/{cid}/relabel", json={"label": "a b"})
    assert r.status_code == 400 and r.json()["detail"]


def test_box_outside_region_is_400(client, session):
    cid = _id_of(session.inputs, "a", region=0)
    r = client.post(f"/api/candidate/{cid}/box", json={"x": 5, "y": 1300, "w": 10, "h": 10})
    assert r.status_code == 400 and r.json()["detail"]


def test_unknown_candidate_is_400(client):
    r = client.post("/api/candidate/nope/accept")
    assert r.status_code == 400 and "nope" in r.json()["detail"]


def test_coverage(client, session):
    j = client.get("/api/coverage").json()
    assert "targets met" in j["text"]
    assert {"lines", "untargeted", "met_count", "total"} <= set(j)
    assert j["lines"] and j["lines"][0]["kind"] in ("single", "ligature")


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "<canvas" in r.text
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert js.headers["content-type"].split(";")[0] in ("text/javascript", "application/javascript")
    # I6/I7 and UI minors (no JS test runner here; behaviour was checked in a browser): pin the mechanisms
    for needle in ("queue = p", "enqueue(refreshCoverage)", "setPointerCapture", "DRAG_PX = 4", "n: advance",
                   "labelInput.value = state.cands[i].decision.label", "sel.blur()"):
        assert needle in js.text, needle
    assert "<kbd>n</kbd> next pending" in r.text


def test_session_json_carries_inconsistencies(tmp_path, synth):  # I5
    store_root = tmp_path / "store"
    assert main(["init", "--store", str(store_root), "--sidecar", str(synth.sidecar_path)]) == 0
    s = open_session(store_root, synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path}, None, NOW)
    try:
        assert TestClient(create_app(s, lambda: NOW)).get("/api/session").json()["inconsistencies"] == []
        s.inconsistencies = ["x is accepted but sample capture-x is missing from the store"]
        j = TestClient(create_app(s, lambda: NOW)).get("/api/session").json()
        assert j["inconsistencies"] == s.inconsistencies
    finally:
        s.close()


def test_box_and_relabel_success(client, session, synth):  # Task 11 extra
    cid = _id_of(session.inputs, "c", region=0)
    b = session.inputs.candidate(cid).bbox
    r = client.post(f"/api/candidate/{cid}/box", json={"x": b.x, "y": b.y, "w": b.w + 2, "h": b.h})
    assert r.status_code == 200 and r.json()["bbox"]["w"] == pytest.approx(b.w + 2)
    assert session.log_for(cid).decisions[cid].bbox.w == pytest.approx(b.w + 2)
    r = client.post(f"/api/candidate/{cid}/relabel", json={"label": "e"})
    assert r.status_code == 200 and r.json()["label"] == "e" and r.json()["kind"] == "single"


@pytest.mark.parametrize("w", [0, -3])
def test_box_non_positive_width_is_400(client, session, w):  # Task 11 extra
    cid = _id_of(session.inputs, "a", region=0)
    b = session.inputs.candidate(cid).bbox
    r = client.post(f"/api/candidate/{cid}/box", json={"x": b.x, "y": b.y, "w": w, "h": b.h})
    assert r.status_code == 400 and r.json()["detail"]


def test_post_response_carries_mirror_flags(client, session, synth):  # Task 11 extra
    t, h = (_id_of(session.inputs, c, region=0) for c in "th")
    pg = session.inputs.pages["p0"]; bt, bh = pg.candidates[t].bbox, pg.candidates[h].bbox
    rb = synth.page.regions[0].bbox
    assert client.post(f"/api/candidate/{t}/accept").json()["flags"] == []
    r = client.post(f"/api/candidate/{t}/box", json={"x": bt.x, "y": rb.y, "w": bh.x + bh.w - bt.x, "h": rb.h})
    assert r.status_code == 200 and "neighbor_swallowed" in r.json()["flags"]  # materialisation flag
    r = client.post(f"/api/candidate/{t}/relabel", json={"label": "zz"})
    assert r.json()["flags"][0] == "label_not_expected" and "neighbor_swallowed" in r.json()["flags"]
    stored = session.store.get_sample(sample_id("capture", t))
    assert stored is not None and stored.label == "zz"
