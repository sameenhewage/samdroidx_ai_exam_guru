"""Source V2 Phase 1 — page layout segmentation.

Given a rendered page image, produce candidate layout regions (text, heading,
figure, table, decorative, unknown) with bounding boxes and reading order.

No OCR happens here. Nothing in this package reads or transcribes text.
"""

from tools.source_factory.layout.model import PageLayout, Region, RegionType

__all__ = ["PageLayout", "Region", "RegionType"]
