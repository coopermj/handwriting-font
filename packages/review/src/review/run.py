"""CLI (`review init | apply | replay | coverage | serve`) and the `Session` helper shared with the app.

Every `apply` action is applied in command-line order through `store_sync.apply` (write-through: the store
mirrors each decision before the directory's `review.json` is saved). `--quality` attaches to the `--accept`
it follows.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hwfont_schema import BBox, CaptureSidecar, GlyphStore, Quality, Target

from review.coverage import format_report, report
from review.decisions import (
    Decision,
    DecisionStatus,
    ReviewLog,
    accept,
    adjust_box,
    load_log,
    reject,
    relabel,
    reset,
    save_log,
)
from review.errors import ReviewError
from review.inputs import ReviewInputs, load_inputs
from review.normalize import sample_id
from review.session import SessionStats, compute_stats
from review.store_sync import apply as sync_apply
from review.store_sync import replay as sync_replay
from review.store_sync import seed_targets

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
QUALITIES = tuple(q.value for q in Quality)


# ---------- session ----------


@dataclass
class Session:
    store: GlyphStore
    inputs: ReviewInputs
    stats: SessionStats
    logs: dict[Path, ReviewLog]
    inconsistencies: list[str] = field(default_factory=list)

    def dir_of(self, candidate_id: str) -> Path:
        """The candidate directory whose page holds `candidate_id` (`ReviewError` if unknown)."""
        return self.inputs.page_of(candidate_id).candidate_dir

    def log_for(self, candidate_id: str) -> ReviewLog:
        return self.logs[self.dir_of(candidate_id)]

    def apply(self, op: Callable[[ReviewLog], ReviewLog], candidate_id: str) -> Decision:
        """Apply a pure log op to the candidate's directory log, write through, keep the new log."""
        d = self.dir_of(candidate_id)
        new_log, decision = sync_apply(self.store, self.inputs, self.stats, self.logs[d], d, op, candidate_id)
        self.logs[d] = new_log
        return decision

    def close(self) -> None:
        self.store.close()


def _frozen_stats(inputs: ReviewInputs, logs: dict[Path, ReviewLog], restats: bool) -> tuple[SessionStats, bool]:
    """The session statistics frozen in the logs (spec ruling 3, amendment I4); computed when no log carries
    them or `restats`. Returns `(stats, changed)` where `changed` means some log must be rewritten."""
    records = [log.stats for log in logs.values() if log.stats is not None]
    if records and not restats:
        if any(r != records[0] for r in records[1:]):
            raise ReviewError(
                "the review logs carry different session statistics; run `review replay --restats` "
                "to recompute them from the loaded pages"
            )
        return SessionStats.from_record(records[0]), len(records) != len(logs)
    return compute_stats(inputs), True


def _inconsistencies(store: GlyphStore, inputs: ReviewInputs, logs: dict[Path, ReviewLog]) -> list[str]:
    """Decisions the store does not mirror: accepted without a sample, or not accepted with one."""
    out: list[str] = []
    for log in logs.values():
        for cid in sorted(log.decisions):
            d = log.decisions[cid]
            sid = sample_id(inputs.session_id, cid)
            present = store.get_sample(sid) is not None
            if d.status is DecisionStatus.accepted and not present:
                out.append(f"{cid} is accepted but sample {sid} is missing from the store")
            elif d.status is not DecisionStatus.accepted and present:
                out.append(f"{cid} is {d.status.value} but sample {sid} is in the store")
    return out


def open_session(
    store_root: Path,
    sidecar: Path,
    candidate_dirs: Sequence[Path],
    rasters: Mapping[str, Path] | None,
    session_id: str | None,
    now: str,
    *,
    restats: bool = False,
) -> Session:
    """Open an initialised store and load inputs, stats and one decision log per candidate directory.

    Session statistics are frozen in the logs: the first open computes them and stores them into every
    loaded log; later opens reuse them (all logs carrying stats must agree). `restats=True` recomputes them
    from the loaded pages and rewrites every log (the caller replays). `Session.inconsistencies` lists every
    decision the store does not mirror.
    """
    dirs = [Path(d) for d in candidate_dirs]
    inputs = load_inputs(Path(sidecar), dirs, rasters, session_id)
    logs = {d: load_log(d, inputs, now) for d in dirs}
    stats, changed = _frozen_stats(inputs, logs, restats)
    store = GlyphStore.open(store_root)
    try:
        if changed:
            record = stats.to_record(list(inputs.pages))
            for d in dirs:
                logs[d] = save_log(d, logs[d].model_copy(update={"stats": record}))
        problems = _inconsistencies(store, inputs, logs)
    except BaseException:
        store.close()
        raise
    return Session(store=store, inputs=inputs, stats=stats, logs=logs, inconsistencies=problems)


# ---------- argument parsing ----------


class _Ordered(argparse.Action):
    """Append `(kind, value)` to the shared `args.actions` list, preserving command-line order."""

    def __call__(self, parser, namespace, values, option_string=None):
        actions = getattr(namespace, self.dest, None) or []
        actions.append((self.const, values))
        setattr(namespace, self.dest, actions)


def _parse_rasters(values: Sequence[str] | None) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for v in values or ():
        pid, sep, path = v.partition("=")
        if not sep or not pid or not path:
            raise ReviewError(f"malformed --raster {v!r}: expected PAGE_ID=PATH")
        out[pid] = Path(path)
    return out


def _split_id(value: str, flag: str, what: str) -> tuple[str, str]:
    cid, sep, rest = value.partition("=")
    if not sep or not cid or not rest:
        raise ReviewError(f"malformed {flag} {value!r}: expected ID={what}")
    return cid, rest


def _parse_box(value: str) -> tuple[str, BBox]:
    cid, rest = _split_id(value, "--box", "x,y,w,h")
    parts = rest.split(",")
    try:
        if len(parts) != 4:
            raise ValueError
        x, y, w, h = (float(p) for p in parts)
    except ValueError:
        raise ReviewError(f"malformed --box {value!r}: expected ID=x,y,w,h") from None
    return cid, BBox(x=x, y=y, w=w, h=h)


def _now(args: argparse.Namespace) -> str:
    return args.now or datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open(args: argparse.Namespace, *, restats: bool = False, warn: bool = True) -> Session:
    session = open_session(
        Path(args.store), Path(args.sidecar), [Path(d) for d in args.candidates],
        _parse_rasters(args.raster), args.session, _now(args), restats=restats,
    )
    if warn and session.inconsistencies:
        for line in session.inconsistencies:
            print(f"warning: {line}", file=sys.stderr)
        print("warning: run `review replay` to make the store mirror the review logs", file=sys.stderr)
    return session


def _fmt(x: float) -> str:
    return f"{x:g}"


def _print_decision(line: str, d: Decision) -> None:
    print(line)
    if d.flags:
        print("  flags: " + ", ".join(d.flags))


# ---------- commands ----------


def init_command(args: argparse.Namespace) -> int:
    root = Path(args.store)
    db = root / "store.db"
    if db.exists() and not args.force:
        raise ReviewError(f"{db} already exists (use --force to reopen it and seed missing targets)")
    path = Path(args.sidecar)
    try:
        sidecar = CaptureSidecar.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewError(f"cannot read sidecar {path}: {exc}") from exc
    targets = None
    if args.targets:
        try:
            raw = json.loads(Path(args.targets).read_text(encoding="utf-8"))
            targets = [Target.model_validate(o) for o in raw]
        except (OSError, ValueError, TypeError) as exc:
            raise ReviewError(f"cannot read targets {args.targets}: {exc}") from exc
    store = GlyphStore.open(root) if db.exists() else GlyphStore.create(root)
    try:
        n = seed_targets(store, sidecar, targets)
    finally:
        store.close()
    print(f"initialised {root}: {n} targets")
    return 0


def _resolve_actions(raw: Sequence[tuple[str, str]], now: str, inputs: ReviewInputs):
    """Turn the ordered `(kind, value)` list into `(line_fn, op, candidate_id)` steps; validates before any write."""
    steps: list[list] = []
    for kind, value in raw:
        if kind == "quality":
            if value not in QUALITIES:
                raise ReviewError(f"--quality {value!r}: expected one of {', '.join(QUALITIES)}")
            if not steps or steps[-1][0] != "accept":
                raise ReviewError("--quality must follow an --accept")
            if steps[-1][2] is not None:
                raise ReviewError(f"--quality given twice for --accept {steps[-1][1]}")
            steps[-1][2] = Quality(value)
        elif kind == "relabel":
            steps.append([kind, *_split_id(value, "--relabel", "LABEL")])
        elif kind == "box":
            steps.append([kind, *_parse_box(value)])
        else:
            steps.append([kind, value, None])
    for step in steps:
        kind, cid, arg = step
        if kind == "accept":
            yield cid, (lambda l, c=cid, q=arg: accept(l, c, now, q)), (
                lambda d: f"accepted {d.candidate_id} '{d.label}' ({d.quality.value})")
        elif kind == "reject":
            yield cid, (lambda l, c=cid: reject(l, c, now)), (lambda d: f"rejected {d.candidate_id}")
        elif kind == "relabel":
            yield cid, (lambda l, c=cid, lab=arg: relabel(l, inputs, c, lab, now)), (
                lambda d: f"relabelled {d.candidate_id} -> '{d.label}'")
        elif kind == "box":
            yield cid, (lambda l, c=cid, b=arg: adjust_box(l, inputs, c, b, now)), (
                lambda d: f"box {d.candidate_id} -> {_fmt(d.bbox.x)},{_fmt(d.bbox.y)},{_fmt(d.bbox.w)},{_fmt(d.bbox.h)}")
        else:  # reset
            yield cid, (lambda l, c=cid: reset(l, inputs, c, now)), (lambda d: f"reset {d.candidate_id}")


def apply_command(args: argparse.Namespace) -> int:
    raw = args.actions or []
    if not raw:
        raise ReviewError("no actions given (use --accept/--reject/--relabel/--box/--reset)")
    now = _now(args)
    session = _open(args)
    try:
        steps = list(_resolve_actions(raw, now, session.inputs))
        for cid, op, line in steps:
            d = session.apply(op, cid)
            _print_decision(line(d), d)
    finally:
        session.close()
    return 0


def replay_command(args: argparse.Namespace) -> int:
    session = _open(args, restats=args.restats, warn=False)
    try:
        if args.restats:
            st = session.stats
            print(f"recomputed session statistics: x-height {st.x_height:.4f} em, "
                  f"half-gap {st.median_half_gap:.4f} em")
        for d, log in session.logs.items():
            new_log = save_log(d, sync_replay(session.store, session.inputs, session.stats, log))
            session.logs[d] = new_log
            print(f"replayed {len(new_log.decisions)} decisions from {d}")
    finally:
        session.close()
    return 0


def coverage_command(args: argparse.Namespace) -> int:
    session = _open(args)
    try:
        print(format_report(report(session.store, list(session.logs.values()))))
    finally:
        session.close()
    return 0


def serve_command(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from review.app import create_app
    except ImportError as exc:
        raise ReviewError(f"the ui extra is not installed ({exc}); pip install 'review[ui]'") from exc
    session = _open(args)
    try:
        app = create_app(session)
        print(f"serving http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        session.close()
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="review", description="Review a CandidateSet into a GlyphStore.")
    sub = ap.add_subparsers(dest="command", required=True)

    def base(name: str, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.add_argument("--store", required=True, help="glyph store root (holds store.db)")
        p.add_argument("--sidecar", required=True, help="capture.sidecar.json (Contract X)")
        return p

    def session_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--candidates", action="append", required=True, metavar="DIR",
                       help="candidate directory (repeatable, one per page)")
        p.add_argument("--raster", action="append", metavar="PAGE_ID=PATH", help="page raster PNG (repeatable)")
        p.add_argument("--session", default=None, help="session id (default: the sidecar file stem)")
        p.add_argument("--now", default=None, help="ISO-8601 updated_at for changed decisions (default: now, UTC)")

    p = base("init", "create the store and seed targets")
    p.add_argument("--targets", default=None, metavar="T.json", help="JSON list of Target objects")
    p.add_argument("--force", action="store_true", help="reopen an existing store; seed targets only if it has none")
    p.set_defaults(func=init_command)

    p = base("apply", "apply decisions in command-line order")
    session_opts(p)
    for flag, kind, meta in [
        ("--accept", "accept", "ID"), ("--quality", "quality", "good|marginal|bad"),
        ("--reject", "reject", "ID"), ("--relabel", "relabel", "ID=LABEL"),
        ("--box", "box", "ID=x,y,w,h"), ("--reset", "reset", "ID"),
    ]:
        p.add_argument(flag, action=_Ordered, dest="actions", const=kind, metavar=meta)
    p.set_defaults(func=apply_command, actions=None)

    p = base("replay", "rebuild the store's samples from every review.json")
    session_opts(p)
    p.add_argument("--restats", action="store_true",
                   help="recompute the session statistics frozen in the review logs, then replay")
    p.set_defaults(func=replay_command)

    p = base("coverage", "print the coverage report")
    session_opts(p)
    p.set_defaults(func=coverage_command)

    p = base("serve", "serve the review UI")
    session_opts(p)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.set_defaults(func=serve_command)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except (ReviewError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
