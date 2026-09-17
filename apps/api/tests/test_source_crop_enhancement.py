import hashlib

import pymupdf

from exam_guru_api.documents.source_reading import SourceCrop, enhance_source_crop


def test_contrast_rendition_is_derived_from_pixels_without_replacing_the_original() -> None:
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
    image.set_rect(image.irect, (220, 170, 30))
    image.set_rect(pymupdf.IRect(30, 20, 90, 60), (220, 30, 50))
    raw = image.tobytes("png")
    original = SourceCrop("a" * 64, hashlib.sha256(raw).hexdigest(), 50, 100, 120, 80, raw)
    enhanced = enhance_source_crop(original)
    assert original.png == raw
    assert original.sha256 == hashlib.sha256(raw).hexdigest()
    assert enhanced.sha256 == hashlib.sha256(enhanced.png).hexdigest()
    assert enhanced.parent_sha256 == original.parent_sha256
    assert (enhanced.left, enhanced.top, enhanced.width, enhanced.height) == (50, 100, 120, 80)
    assert enhanced.metadata()["transform"] == "grayscale-otsu.v1"
    transformed = pymupdf.Pixmap(enhanced.png)
    assert transformed.pixel(1, 1) == (255,)
    assert transformed.pixel(60, 40) == (0,)


def test_blank_crop_remains_blank_after_contrast_processing() -> None:
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20), False)
    image.clear_with(255)
    raw = image.tobytes("png")
    crop = SourceCrop("a" * 64, hashlib.sha256(raw).hexdigest(), 0, 0, 30, 20, raw)
    assert set(pymupdf.Pixmap(enhance_source_crop(crop).png).samples) == {255}
