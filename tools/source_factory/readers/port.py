"""The reader port. Every source reader implements exactly this.

Keeping the port this small is what lets the benchmark compare a local VLM, a
classic OCR engine and an imported Codex/Astra document on equal terms, and what
lets a reader be dropped in Phase 7 without touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class ReadRequest:
    """One region of one rendered page, handed to a reader."""

    crop: np.ndarray  # BGR pixels of the region
    region_id: str
    region_type: str  # layout type: text | heading | figure | table | decorative | unknown
    language: str  # "sinhala" | "tamil" | "english" | "mixed"
    page_number: int
    document_id: str


@dataclass
class ReadResult:
    """What a reader proposes. Never authority.

    `text` is exact visible Unicode as the reader saw it, with no normalisation.
    `abstained` is a legitimate, correct outcome: a reader that cannot read this
    region must say so rather than invent content.
    """

    reader: str
    region_id: str
    text: str
    abstained: bool = False
    failure: str | None = None
    seconds: float = 0.0
    peak_vram_bytes: int | None = None
    raw: str | None = None  # untouched provider payload, for audit
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.failure is None and not self.abstained

    def to_json(self) -> dict:
        payload: dict = {
            "reader": self.reader,
            "region_id": self.region_id,
            "text": self.text,
            "abstained": self.abstained,
            "seconds": round(self.seconds, 3),
        }
        if self.failure:
            payload["failure"] = self.failure
        if self.peak_vram_bytes is not None:
            payload["peak_vram_bytes"] = self.peak_vram_bytes
        if self.meta:
            payload["meta"] = self.meta
        return payload


@runtime_checkable
class SourceReader(Protocol):
    """A language-specialist reader."""

    name: str
    languages: tuple[str, ...]

    def load(self) -> None:
        """Bring the model into memory. Separate from read() so load cost is measurable."""

    def read(self, request: ReadRequest) -> ReadResult: ...

    def unload(self) -> None:
        """Release VRAM so the next reader in a benchmark starts from a clean device."""


class ReaderUnavailable(RuntimeError):
    """The reader cannot run here. Reported as a benchmark result, never hidden."""
