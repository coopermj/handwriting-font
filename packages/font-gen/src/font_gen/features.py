"""OpenType feature generation: GlyphPlan → `.fea` text (classes, ligature tiers, positional forms, ring cycling, aalt, GDEF).

Lookups are defined in the order they must run (LookupList order = definition order): ligature tiers longest first,
then `TO_*` + `POS`, then `STEP*` + `CYCLE`. A lookup that would be empty is omitted together with every rule that
references it — a chain rule naming an empty lookup aborts feaLib compilation.
"""

from __future__ import annotations

from typing import Literal

from hwfont_schema import Kind

from font_gen.load import Pos
from font_gen.naming import POS_ORDER, base_name
from font_gen.qa import Finding
from font_gen.select import NOTDEF, GlyphPlan, RingKey

CycleMode = Literal["stream", "word"]

POS_CONTEXT_ORDER: tuple[Pos, ...] = ("medi", "fina", "init", "isol")
TO_LOOKUPS: dict[Pos, str] = {"medi": "TO_MEDI", "fina": "TO_FINA", "init": "TO_INIT", "isol": "TO_ISOL"}

LETTER = "@LETTER"
BASE = "@BASE"
TRANSPARENT = "@TRANSPARENT"
TRANSPARENT_DEPTH = 2

# (backtrack, lookahead) for the four mutually exclusive word contexts, in POS_CONTEXT_ORDER
_CONTEXT: dict[Pos, tuple[str, str]] = {
    "medi": (LETTER, LETTER),
    "fina": (LETTER, ""),
    "init": ("", LETTER),
    "isol": ("", ""),
}

Lookup = tuple[str, list[str]]
Ligature = tuple[tuple[str, ...], str, frozenset[Pos]]  # components, ligature glyph, allowed positions


# ---------- classes ----------


def letter_class(plan: GlyphPlan) -> list[str]:
    return sorted(plan.letters)


def _default_rings(plan: GlyphPlan) -> list[tuple[RingKey, tuple[str, ...]]]:
    return sorted(((k, r) for k, r in plan.rings.items() if k[1] is None), key=lambda kr: kr[1][0])


def base_class(plan: GlyphPlan) -> list[str]:
    return [ring[0] for _, ring in _default_rings(plan)]


def _cycling_rings(plan: GlyphPlan) -> list[tuple[str, ...]]:
    return [ring for ring in plan.rings.values() if len(ring) >= 2]


def state_classes(plan: GlyphPlan) -> list[list[str]]:
    rings = _cycling_rings(plan)
    return [sorted({ring[k % len(ring)] for ring in rings}) for k in range(plan.ring_n)]


def transparent_class(plan: GlyphPlan) -> list[str]:
    return sorted(plan.transparent)


# ---------- ligatures ----------


def _rule(backtrack: str, marked: str, lookahead: str, *, by: str | None = None, inner: str | None = None) -> str:
    """`sub <back> <marked> [lookup <inner>] <ahead> [by <lig>];`, or the `ignore sub` guard when neither is given."""
    seq = " ".join(part for part in (backtrack, marked, inner and f"lookup {inner}", lookahead) if part)
    if by is not None:
        return f"sub {seq} by {by};"
    return f"sub {seq};" if inner is not None else f"ignore sub {seq};"


def _contextual_rules(marked: str, allowed: frozenset[Pos], *, by: str | None = None,
                      inner: dict[Pos, str] | None = None) -> list[str]:
    """The four-row context shape: a rule per context in POS_CONTEXT_ORDER, an `ignore` guard where not allowed."""
    rows: list[str] = []
    for pos in POS_CONTEXT_ORDER:
        back, ahead = _CONTEXT[pos]
        if pos in allowed:
            rows.append(_rule(back, marked, ahead, by=by, inner=inner[pos] if inner else None))
        elif pos != "isol":
            rows.append(_rule(back, marked, ahead))
    return rows


def ligature_rules(components: tuple[str, ...], lig: str, allowed: frozenset[Pos]) -> list[str]:
    marked = " ".join(f"{c}'" for c in components)
    if len(allowed) == len(POS_ORDER):
        return [f"sub {marked} by {lig};"]
    return _contextual_rules(marked, allowed, by=lig)


def _ligature_tiers(plan: GlyphPlan) -> list[tuple[str, list[Ligature]]]:
    """`(lookup name, ligatures)` in LookupList order: per length descending, the shared tier then one per restricted."""
    by_len: dict[int, list[Ligature]] = {}
    for lig, (components, allowed) in plan.ligatures.items():
        by_len.setdefault(len(components), []).append((components, lig, allowed))
    tiers: list[tuple[str, list[Ligature]]] = []
    for n in sorted(by_len, reverse=True):
        ligs = sorted(by_len[n], key=lambda lig: lig[0])
        shared = [lig for lig in ligs if len(lig[2]) == len(POS_ORDER)]
        if shared:
            tiers.append((f"LIGA_{n}", shared))
        for lig in ligs:
            if len(lig[2]) < len(POS_ORDER):
                tiers.append((f"LIGA_{n}_{lig[1]}", [lig]))
    return tiers


def ligature_lookups(plan: GlyphPlan) -> list[Lookup]:
    return [(name, [row for comps, lig, allowed in ligs for row in ligature_rules(comps, lig, allowed)])
            for name, ligs in _ligature_tiers(plan)]


def _context_of(i: int, n: int, length: int) -> Pos:
    before, after = i > 0, i + n < length
    if before and after:
        return "medi"
    if before:
        return "fina"
    if after:
        return "init"
    return "isol"


def resolve_ligatures(plan: GlyphPlan, word: str) -> tuple[str, ...]:
    """Reference oracle: what the ligature lookups do to an all-alphabetic `word` typed in isolation."""
    seq = [base_name(c) for c in word]
    for _, ligs in _ligature_tiers(plan):
        i = 0
        while i < len(seq):
            step = 1
            for components, lig, allowed in ligs:
                n = len(components)
                if tuple(seq[i:i + n]) != components:
                    continue
                ctx = _context_of(i, n, len(seq))
                if ctx in allowed:
                    seq[i:i + n] = [lig]
                elif ctx != "isol":
                    step = n
                break
            i += step
    return tuple(seq)


def _overlap(a: str, b: str) -> tuple[str, str, int] | None:
    """`(x, y, k)`: the longest `k` with `x[-k:] == y[:k]` (proper, non-empty), `x` leading on a tie; None when none."""
    best: tuple[str, str, int] | None = None
    for x, y in ((a, b), (b, a)):
        for k in range(min(len(x), len(y)) - 1, 0, -1):
            if x[-k:] == y[:k]:
                if best is None or k > best[2]:
                    best = (x, y, k)
                break
    return best


def ligature_overlaps(plan: GlyphPlan) -> list[Finding]:
    by_name = plan.by_name()
    labels = sorted(by_name[lig].label for lig in plan.ligatures)
    findings: list[Finding] = []
    for idx, a in enumerate(labels):
        for b in labels[idx + 1:]:
            if a in b or b in a:
                word = max(a, b, key=len)
            else:
                ov = _overlap(a, b)
                if ov is None:
                    continue
                x, y, k = ov
                word = x + y[k:]
            resolution = "longer tier first" if len(a) != len(b) else "unconditional before restricted, leftmost first"
            expect = list(resolve_ligatures(plan, word))
            findings.append(Finding(
                "info", "ligature_overlap",
                f"ligatures {a!r} and {b!r} compete in {word!r}: {resolution}; expect {' '.join(expect)}",
                data={"pair": [a, b], "word": word, "resolution": resolution, "expect": expect},
            ))
    return sorted(findings, key=lambda f: f.data["pair"])


# ---------- positional forms ----------


def positional_lookups(plan: GlyphPlan) -> list[Lookup]:
    lookups: list[Lookup] = []
    present: set[Pos] = set()
    for pos in POS_CONTEXT_ORDER:
        rows = sorted(f"sub {base} by {forms[pos]};" for base, forms in plan.positional.items() if pos in forms)
        if rows:
            lookups.append((TO_LOOKUPS[pos], rows))
            present.add(pos)
    if not present:
        return []
    lookups.append(("POS", _contextual_rules(f"{BASE}'", frozenset(present), inner=TO_LOOKUPS)))
    return lookups


# ---------- variant cycling ----------


def cycle_lookups(plan: GlyphPlan, cycle_mode: CycleMode) -> list[Lookup]:
    rings = _cycling_rings(plan)
    lookups: list[Lookup] = []
    cycle: list[str] = []
    for k in range(1, plan.ring_n):
        rows = sorted(f"sub {ring[0]} by {ring[k % len(ring)]};" for ring in rings if ring[k % len(ring)] != ring[0])
        if not rows:
            continue
        step = f"STEP{k}"
        lookups.append((step, rows))
        depths = range(TRANSPARENT_DEPTH + 1) if cycle_mode == "stream" else range(1)
        for depth in depths:
            back = " ".join([f"@ST{k - 1}", *([TRANSPARENT] * depth)])
            cycle.append(f"sub {back} @ST0' lookup {step};")
    if cycle:
        lookups.append(("CYCLE", cycle))
    return lookups


# ---------- aalt, GDEF ----------


def aalt_rules(plan: GlyphPlan) -> list[str]:
    rows: list[str] = []
    for (label, _), ring in _default_rings(plan):
        alts = list(ring[1:])
        for pos in POS_ORDER:
            alts.extend(plan.rings.get((label, pos), ()))
        if alts:
            rows.append(f"sub {ring[0]} from [{' '.join(alts)}];")
    return rows


def gdef_block(plan: GlyphPlan, carets: dict[str, tuple[int, ...]]) -> str:
    bases = sorted(g.name for g in plan.glyphs if g.kind != Kind.ligature and g.name != NOTDEF.name)
    ligs = sorted(g.name for g in plan.glyphs if g.kind == Kind.ligature)
    lines = [f"  GlyphClassDef [{' '.join(bases)}], [{' '.join(ligs)}], , ;"]
    for name in sorted(carets):
        if carets[name]:
            lines.append(f"  LigatureCaretByPos {name} {' '.join(str(c) for c in carets[name])};")
    return "table GDEF {\n" + "\n".join(lines) + "\n} GDEF;\n"


# ---------- assembly ----------


def _lookup_block(name: str, rows: list[str]) -> str:
    return f"lookup {name} {{\n" + "".join(f"  {row}\n" for row in rows) + f"}} {name};\n"


def _feature_block(tag: str, rows: list[str]) -> str:
    return f"feature {tag} {{\n" + "".join(f"  {row}\n" for row in rows) + f"}} {tag};\n"


def _class_line(name: str, members: list[str]) -> str:
    return f"{name} = [{' '.join(members)}];\n"


def generate_features(
    plan: GlyphPlan, carets: dict[str, tuple[int, ...]], *, digest12: str, cycle_mode: CycleMode = "stream",
    version: str = "0.1.0",
) -> str:
    out: list[str] = [
        f"# Generated by font-gen {version} from store {digest12} — do not edit; regenerate.\n",
        "languagesystem DFLT dflt;\n",
        "languagesystem latn dflt;\n",
        "\n# ---------- classes ----------\n",
        _class_line(LETTER, letter_class(plan)),
        _class_line(BASE, base_class(plan)),
    ]
    if _cycling_rings(plan):
        out.extend(_class_line(f"@ST{k}", members) for k, members in enumerate(state_classes(plan)))
    out.append(_class_line(TRANSPARENT, transparent_class(plan)))

    sections = (
        ("ligatures", ligature_lookups(plan)),
        ("positional forms", positional_lookups(plan)),
        ("variant cycling", cycle_lookups(plan, cycle_mode)),
    )
    for title, lookups in sections:
        if lookups:
            out.append(f"\n# ---------- {title} ----------\n")
            out.extend(_lookup_block(name, rows) for name, rows in lookups)

    out.append("\n# ---------- features ----------\n")
    liga = [f"lookup {name};" for name, _ in sections[0][1]]
    if liga:
        out.append(_feature_block("liga", liga))
        out.append(_feature_block("clig", liga))
    calt = [f"lookup {name};" for name in ("POS", "CYCLE") if any(n == name for n, _ in sections[1][1] + sections[2][1])]
    if calt:
        out.append(_feature_block("calt", calt))
    aalt = aalt_rules(plan)
    if aalt:
        out.append(_feature_block("aalt", aalt))
    out.append("\n" + gdef_block(plan, carets))
    return "".join(out)
