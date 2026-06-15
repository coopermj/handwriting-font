from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any

import yaml
from hwfont_schema import Kind, Target

DEFAULT_GLYPH_COUNT = 12
DEFAULT_LIGATURE_COUNT = 8
DEFAULT_GLYPHS = string.ascii_letters + string.digits + ".,!?'\"-;:()"
DEFAULT_LIGATURES = ["fi", "fl", "ff", "ffi", "ffl", "eft", "fore", "ough", "tion", "ing"]


def _build(
    glyph_chars: str,
    glyph_count: int,
    ligatures: list[str],
    ligature_count: int,
    overrides: dict[str, int],
) -> list[Target]:
    seen: set[str] = set()
    targets: list[Target] = []

    for ch in glyph_chars:
        if ch in seen:
            raise ValueError(f"duplicate target label: {ch!r}")
        seen.add(ch)
        targets.append(
            Target(label=ch, kind=Kind.single, required_count=overrides.get(ch, glyph_count))
        )

    for lig in ligatures:
        if len(lig) < 2:
            raise ValueError(f"ligature must be at least 2 characters: {lig!r}")
        if lig in seen:
            raise ValueError(f"duplicate target label: {lig!r}")
        seen.add(lig)
        targets.append(
            Target(
                label=lig, kind=Kind.ligature, required_count=overrides.get(lig, ligature_count)
            )
        )

    return targets


def default_targets() -> list[Target]:
    return _build(
        DEFAULT_GLYPHS, DEFAULT_GLYPH_COUNT, DEFAULT_LIGATURES, DEFAULT_LIGATURE_COUNT, {}
    )


def load_target_spec(path: str | Path) -> list[Target]:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = (
        json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    )
    glyphs = data.get("glyphs", {})
    ligatures = data.get("ligatures", {})
    return _build(
        glyph_chars=glyphs.get("include", DEFAULT_GLYPHS),
        glyph_count=int(glyphs.get("count", DEFAULT_GLYPH_COUNT)),
        ligatures=list(ligatures.get("items", DEFAULT_LIGATURES)),
        ligature_count=int(ligatures.get("count", DEFAULT_LIGATURE_COUNT)),
        overrides={k: int(v) for k, v in data.get("overrides", {}).items()},
    )
