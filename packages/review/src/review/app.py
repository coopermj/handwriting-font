"""FastAPI app over an open review `Session`: JSON API plus the single static canvas page.

Every change goes through `Session.apply` (write-through to the store, then the directory's
`review.json`). `ReviewError` becomes `400 {"detail": message}`.
"""

from __future__ import annotations

import dataclasses
import io
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from importlib.resources import files

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from hwfont_schema import BBox, GlyphStore, Quality
from pydantic import BaseModel

from review.coverage import format_report, report
from review.decisions import accept, adjust_box, reject, relabel, reset, set_quality
from review.errors import ReviewError
from review.ink import composite_page
from review.run import Session
from review.session import effective_context

STATIC_DIR = files("review") / "static"


class AcceptBody(BaseModel):
    quality: Quality | None = None


class QualityBody(BaseModel):
    quality: Quality | None


class LabelBody(BaseModel):
    label: str


class BoxBody(BaseModel):
    x: float
    y: float
    w: float
    h: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_json(session: Session) -> dict:
    st = session.stats
    pages = []
    for pg in session.inputs.pages.values():
        p = pg.page
        log = session.logs[pg.candidate_dir]
        pages.append({
            "id": p.id,
            "width_px": p.width_px,
            "height_px": p.height_px,
            "regions": [
                {"id": r.id, "bbox": r.bbox.model_dump(mode="json"), "baseline_y": r.baseline_y,
                 "expected_transcript": r.expected_transcript}
                for r in p.regions
            ],
            "candidates": [
                {
                    "id": c.id,
                    "region_id": c.region_id,
                    "confidence": c.confidence,
                    "status": c.status.value,
                    "context": effective_context(pg, c.region_id, c.id).model_dump(mode="json"),
                    "original_bbox": c.bbox.model_dump(mode="json"),
                    "decision": log.decisions[c.id].model_dump(mode="json"),
                }
                for c in pg.candidate_set.candidates  # manifest order: lowest confidence first
            ],
        })
    return {
        "session_id": session.inputs.session_id,
        "stats": {"x_height": st.x_height, "median_half_gap": st.median_half_gap, "flags": list(st.flags)},
        "inconsistencies": list(session.inconsistencies),
        "pages": pages,
    }


def _coverage_json(session: Session) -> dict:
    rep = report(session.store, list(session.logs.values()))
    lines = []
    for line in rep.lines:
        d = dataclasses.asdict(line)
        d["kind"] = line.kind.value
        lines.append(d)
    return {
        "lines": lines,
        "untargeted": [list(u) for u in rep.untargeted],
        "met_count": rep.met_count,
        "total": rep.total,
        "text": format_report(rep),
    }


def _thread_safe_store(store: GlyphStore) -> GlyphStore:
    """Reopen `store` on a connection usable from the server's worker threads.

    `GlyphStore.open` uses sqlite3's default `check_same_thread=True`, but FastAPI runs sync handlers in a
    thread pool; every handler holds the app's lock, so access stays serialised.
    """
    conn = sqlite3.connect(store.root / "store.db", check_same_thread=False)
    shared = GlyphStore(store.root, conn)
    store.close()
    return shared


def create_app(session: Session, now: Callable[[], str] | None = None) -> FastAPI:
    """Build the app. Replaces `session.store` with a thread-shareable connection to the same store
    (call from the thread that opened the session); `session.close()` still closes it."""
    clock = now or _utc_now
    inputs = session.inputs
    session.store = _thread_safe_store(session.store)
    lock = threading.Lock()
    app = FastAPI(title="review")

    @app.exception_handler(ReviewError)
    async def _review_error(_request: Request, exc: ReviewError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def change(op: Callable, cid: str) -> dict:
        with lock:
            return session.apply(op, cid).model_dump(mode="json")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"), media_type="text/html")

    @app.get("/api/session")
    def get_session() -> dict:
        with lock:
            return _session_json(session)

    @app.get("/api/page/{page_id}/image.png")
    def page_image(page_id: str) -> Response:
        pg = inputs.pages.get(page_id)
        if pg is None:
            raise HTTPException(404, detail=f"unknown page {page_id!r}")
        buf = io.BytesIO()
        composite_page(pg).save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")

    @app.get("/api/candidate/{cid}/crop.png")
    def crop(cid: str) -> Response:
        for pg in inputs.pages.values():
            if cid in pg.crops:
                return Response(content=pg.crops[cid], media_type="image/png")
        raise HTTPException(404, detail=f"no crop for candidate {cid!r}")

    @app.post("/api/candidate/{cid}/accept")
    def post_accept(cid: str, body: AcceptBody | None = None) -> dict:
        q = body.quality if body else None
        t = clock()
        return change(lambda log: accept(log, cid, t, q), cid)

    @app.post("/api/candidate/{cid}/reject")
    def post_reject(cid: str) -> dict:
        t = clock()
        return change(lambda log: reject(log, cid, t), cid)

    @app.post("/api/candidate/{cid}/relabel")
    def post_relabel(cid: str, body: LabelBody) -> dict:
        t = clock()
        return change(lambda log: relabel(log, inputs, cid, body.label, t), cid)

    @app.post("/api/candidate/{cid}/box")
    def post_box(cid: str, body: BoxBody) -> dict:
        try:
            bbox = BBox(x=body.x, y=body.y, w=body.w, h=body.h)
        except ValueError as exc:
            raise ReviewError(f"invalid box: {exc}") from exc
        t = clock()
        return change(lambda log: adjust_box(log, inputs, cid, bbox, t), cid)

    @app.post("/api/candidate/{cid}/quality")
    def post_quality(cid: str, body: QualityBody) -> dict:
        if body.quality is None:
            raise ReviewError("quality is required (good, marginal or bad)")
        t = clock()
        q = body.quality
        return change(lambda log: set_quality(log, cid, q, t), cid)

    @app.post("/api/candidate/{cid}/reset")
    def post_reset(cid: str) -> dict:
        t = clock()
        return change(lambda log: reset(log, inputs, cid, t), cid)

    @app.get("/api/coverage")
    def coverage() -> dict:
        with lock:
            return _coverage_json(session)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
