"""Per-session statistics and within-region unit order.

Everything here depends on the ORIGINAL candidates alone (spec ruling 3): vision
boxes, labels and strokes as loaded, never the decision log, so neighbour geometry
is fixed at load and a review operation changes exactly one sample.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import numpy as np
from hwfont_schema import Context, PositionInWord, Region

from review.ink import candidate_extent, strokes_to_polylines
from review.inputs import PageInputs, ReviewInputs

XH_LETTERS = "acemnorsuvwxz"
XH_PERCENTILE = 90
X_HEIGHT_DEFAULT = 0.45
HALF_GAP_DEFAULT = 0.05


@dataclass(frozen=True)
class SessionStats:
    x_height: float
    median_half_gap: float
    n_x_height_letters: int
    n_gaps: int
    flags: tuple[str, ...]


def scale(region: Region) -> float:
    """em per page px."""
    return 1.0 / region.bbox.h


def em_y(region: Region, y_px: float) -> float:
    """Page-px y (down) to em y (up, 0 at the region baseline)."""
    return (region.baseline_y - y_px) * scale(region)


def unit_order(page: PageInputs, region_id: str) -> list[str]:
    """Region candidate ids by centre x of the original box, ties by id."""

    def key(cid: str) -> tuple[float, str]:
        b = page.candidates[cid].bbox
        return (b.x + b.w / 2, cid)

    return sorted(page.candidate_ids(region_id), key=key)


def _adjacent(page: PageInputs, region_id: str, candidate_id: str) -> tuple[str | None, str | None]:
    order = unit_order(page, region_id)
    i = order.index(candidate_id)
    left = order[i - 1] if i > 0 else None
    right = order[i + 1] if i + 1 < len(order) else None
    return left, right


def _unit_map(transcript: str) -> list[tuple[str, int, int, int]]:
    """For each non-space char (in order): (char, word_index, pos_in_word, word_len)."""
    out: list[tuple[str, int, int, int]] = []
    words = transcript.split()
    word_index, pos, prev_space = -1, 0, True
    for ch in transcript:
        if ch.isspace():
            prev_space = True
            continue
        if prev_space:
            word_index, pos, prev_space = word_index + 1, 0, False
        out.append((ch, word_index, pos, len(words[word_index])))
        pos += 1
    return out


def _span_context(transcript: str, label: str, offset: int, length: int) -> Context:
    """Context of the `length` units starting at character offset `offset` (same rules as ingest's
    `derive_context(transcript, label, offset, length)`; implemented locally, not imported)."""
    units = _unit_map(transcript)
    if offset < 0 or offset >= len(units):
        return Context(source_word=label, position_in_word=PositionInWord.isolated)
    _, word_index, pos, word_len = units[offset]
    n = max(length, 1)
    at_start, at_end = pos == 0, pos + n - 1 >= word_len - 1
    if at_start and at_end:
        position = PositionInWord.isolated
    elif at_start:
        position = PositionInWord.initial
    elif at_end:
        position = PositionInWord.final
    else:
        position = PositionInWord.medial
    right = offset + n
    return Context(
        source_word=transcript.split()[word_index],
        left_neighbor=None if at_start else units[offset - 1][0],
        right_neighbor=units[right][0] if not at_end and right < len(units) else None,
        position_in_word=position,
    )


def effective_context(page: PageInputs, region_id: str, candidate_id: str) -> Context:
    """The candidate's Context, re-derived from the transcript when the region's ORIGINAL labels in unit
    order spell it (whitespace removed) — cumulative character offsets, so a box after a ligature is not
    shifted — else the candidate's own Context unchanged."""
    region = page.region(region_id)
    order = unit_order(page, region_id)
    labels = [page.candidates[cid].label for cid in order]
    if "".join(labels) != "".join(region.expected_transcript.split()):
        return page.candidates[candidate_id].context
    i = order.index(candidate_id)
    offset = sum(len(lab) for lab in labels[:i])
    return _span_context(region.expected_transcript, labels[i], offset, len(labels[i]))


def _agree(page: PageInputs, region_id: str, candidate_id: str) -> tuple[str | None, str | None, bool, bool]:
    """Adjacent ids and, per side, whether the effective context names a neighbour there that the adjacent
    unit's original label agrees with (an empty label never agrees)."""
    ctx = effective_context(page, region_id, candidate_id)
    left, right = _adjacent(page, region_id, candidate_id)
    left_label = page.candidates[left].label if left is not None else ""
    right_label = page.candidates[right].label if right is not None else ""
    left_ok = ctx.left_neighbor is not None and left_label != "" and left_label[-1] == ctx.left_neighbor
    right_ok = ctx.right_neighbor is not None and right_label != "" and right_label[0] == ctx.right_neighbor
    return (left if left_ok else None), (right if right_ok else None), left_ok, right_ok


def neighbours(page: PageInputs, region_id: str, candidate_id: str) -> tuple[str | None, str | None]:
    """Adjacent ids in unit order, each only where Context names a neighbour on that side
    and the adjacent unit's original label agrees."""
    left, right, _, _ = _agree(page, region_id, candidate_id)
    return left, right


def context_mismatch(page: PageInputs, region_id: str, candidate_id: str) -> tuple[bool, bool]:
    """Per side: the effective context names a neighbour, an adjacent unit exists, and its label disagrees."""
    ctx = effective_context(page, region_id, candidate_id)
    left, right = _adjacent(page, region_id, candidate_id)
    _, _, left_ok, right_ok = _agree(page, region_id, candidate_id)
    return (
        ctx.left_neighbor is not None and left is not None and not left_ok,
        ctx.right_neighbor is not None and right is not None and not right_ok,
    )


def candidate_top_em(page: PageInputs, candidate_id: str) -> float | None:
    """XH_PERCENTILE of stroke y_em, or the ink top for a raster-only candidate; None without ink."""
    cand = page.candidates[candidate_id]
    region = page.region(cand.region_id)
    sd = page.strokes.get(candidate_id)
    if sd is not None:
        ys = [em_y(region, y) for pl in strokes_to_polylines(sd) for _, y in pl]
        if ys:
            return float(np.percentile(ys, XH_PERCENTILE))
    ext = candidate_extent(cand, None, page.crops.get(candidate_id))
    if ext is None:
        return None
    return em_y(region, ext.ymin)


def compute_stats(inputs: ReviewInputs) -> SessionStats:
    tops: list[float] = []
    half_gaps: list[float] = []
    for page in inputs.pages.values():
        for region in page.page.regions:
            s = scale(region)
            for cid in unit_order(page, region.id):
                cand = page.candidates[cid]
                if cand.label in XH_LETTERS and len(cand.label) == 1:
                    top = candidate_top_em(page, cid)
                    if top is not None:
                        tops.append(top)
                _, right = neighbours(page, region.id, cid)
                if right is None:
                    continue
                el = candidate_extent(cand, page.strokes.get(cid), page.crops.get(cid))
                er = candidate_extent(page.candidates[right], page.strokes.get(right), page.crops.get(right))
                if el is None or er is None:
                    continue
                half_gaps.append(0.5 * (er.xmin - el.xmax) * s)

    flags: list[str] = []
    if tops:
        x_height = float(statistics.median(tops))
    else:
        x_height = X_HEIGHT_DEFAULT
        flags.append("x_height_assumed")
    if half_gaps:
        half_gap = float(statistics.median(half_gaps))
    else:
        half_gap = HALF_GAP_DEFAULT
        flags.append("half_gap_assumed")
    return SessionStats(
        x_height=x_height,
        median_half_gap=half_gap,
        n_x_height_letters=len(tops),
        n_gaps=len(half_gaps),
        flags=tuple(flags),
    )
