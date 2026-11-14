"""QA findings: the one record type every stage reports problems with."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["error", "warn", "info"]


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str
    label: str | None = None
    sample_id: str | None = None
    glyph: str | None = None
    data: dict = field(default_factory=dict)
