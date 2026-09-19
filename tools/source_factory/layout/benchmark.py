"""Fixed Phase 1 benchmark pages from the real Grade 5 Sinhala teacher guide.

Document: `.exam-guru-data/source-content/grade-05/sinhala/mawbasa-teacher-guide`
(293 pages rendered at 300 dpi; the PDF and its renders are never committed).

Membership is fixed. Do not swap a page out because the detector struggles on
it; fix the detector instead.
"""

from __future__ import annotations

PAGES: tuple[tuple[int, str], ...] = (
    (4, "single-column prose, front matter, two-up signature block, no running bars"),
    (152, "dense two-column prose with inline green activity heading bars"),
    (156, "two-column prose, activity heading bars, verified ground-truth page"),
    (157, "figure crossing the centre gutter above two-column prose"),
    (163, "full-width decorative bars and panel above a tinted two-column body"),
    (171, "two-column prose with a ruled, tinted table in the right column"),
    (186, "two-column prose with stacked figures and a caption inside one column"),
    (197, "portrait figures straddling both columns above two-column prose"),
)

NUMBERS: tuple[int, ...] = tuple(page for page, _ in PAGES)
