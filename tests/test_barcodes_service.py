# Unit tests for the pyzbar barcode/QR detection service (detect_barcodes).
# Only the native libzbar boundary (pyzbar.decode) is stubbed; the polygon/rect
# bbox math and normalization run for real against a real grayscale PIL image.
from unittest.mock import patch

from PIL import Image
from pyzbar.pyzbar import Decoded, Point, Rect

from app.services.barcodes import detect_barcodes


def _pil_image() -> Image.Image:
    return Image.new("RGB", (612, 792), color=(255, 255, 255))


def _decoded(
    *, code_type: str, data: bytes, rect: Rect, polygon: list[Point]
) -> Decoded:
    return Decoded(
        data=data, type=code_type, rect=rect, polygon=polygon, quality=1, orientation="UP"
    )


def test_detect_barcodes_uses_polygon_for_bbox() -> None:
    img = _pil_image()
    # Polygon is a slightly rotated quad; rect is deliberately different so a
    # passing assertion proves the polygon branch ran (and did not raise).
    polygon = [Point(10, 20), Point(50, 22), Point(48, 60), Point(12, 58)]
    decoded = _decoded(
        code_type="QRCODE",
        data=b"https://example.com",
        rect=Rect(0, 0, 100, 100),
        polygon=polygon,
    )
    with patch("app.services.barcodes.decode", return_value=[decoded]):
        results = detect_barcodes(img)

    assert len(results) == 1
    result = results[0]
    assert result.entity_type == "QR_CODE"
    assert result.text == "https://example.com"
    # bbox derived from polygon min/max, normalized and rounded to 4 decimal places
    assert result.bbox.left == round(10 / img.width, 4)
    assert result.bbox.top == round(20 / img.height, 4)
    assert result.bbox.width == round((50 - 10) / img.width, 4)
    assert result.bbox.height == round((60 - 20) / img.height, 4)


def test_detect_barcodes_falls_back_to_rect_when_polygon_empty() -> None:
    img = _pil_image()
    decoded = _decoded(
        code_type="CODE128",
        data=b"012345678905",
        rect=Rect(15, 25, 30, 35),
        polygon=[],
    )
    with patch("app.services.barcodes.decode", return_value=[decoded]):
        results = detect_barcodes(img)

    assert len(results) == 1
    result = results[0]
    assert result.entity_type == "BARCODE"
    assert result.text == "012345678905"
    # bbox derived from rect, normalized and rounded to 4 decimal places
    assert result.bbox.left == round(15 / img.width, 4)
    assert result.bbox.top == round(25 / img.height, 4)
    assert result.bbox.width == round(30 / img.width, 4)
    assert result.bbox.height == round(35 / img.height, 4)


def test_detect_barcodes_no_codes_returns_empty() -> None:
    img = _pil_image()
    with patch("app.services.barcodes.decode", return_value=[]):
        results = detect_barcodes(img)

    assert results == []
