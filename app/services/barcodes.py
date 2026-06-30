"""Barcode and QR code detection via pyzbar.

Wraps pyzbar.decode() and maps the result to BarcodeDetection dataclasses
with normalized bounding boxes matching the BoundingBox schema used throughout
the pipeline.

Accepts any PIL Image — callers are responsible for converting their native
format (fitz.Pixmap, raw bytes, etc.) to PIL before calling. Separating the
format conversion from the detection logic keeps this module free of PDF-specific
dependencies (fitz) and reusable by both the PDF and image routers.

pyzbar quality is an unscaled relative integer, not a 0–1 probability, so
confidence is set to 1.0 — detection is binary (fully decoded or omitted).
Bounding boxes are derived from the polygon field for a tighter fit on
rotated symbols.
"""

from dataclasses import dataclass

from PIL import Image
from pyzbar.pyzbar import decode

from app.schemas import BoundingBox


@dataclass
class BarcodeDetection:
    entity_type: str  # "QR_CODE" or "BARCODE"
    text: str
    bbox: BoundingBox


def detect_barcodes(img: Image.Image) -> list[BarcodeDetection]:
    gray = img.convert("L")
    codes = decode(gray)
    img_width, img_height = gray.size
    results = []
    for code in codes:
        entity_type = "QR_CODE" if code.type == "QRCODE" else "BARCODE"
        text = code.data.decode("utf-8", errors="replace")

        left = code.rect.left
        top = code.rect.top
        width = code.rect.width
        height = code.rect.height

        # Polygon gives a tighter fit on rotated symbols. Some 1D symbologies
        # return an empty polygon, so rect is the safe default.
        if code.polygon:
            xs = [p.x for p in code.polygon]
            ys = [p.y for p in code.polygon]
            left = min(xs)
            top = min(ys)
            width = max(xs) - min(xs)
            height = max(ys) - min(ys)

        results.append(
            BarcodeDetection(
                entity_type=entity_type,
                text=text,
                bbox=BoundingBox(
                    left=left / img_width,
                    top=top / img_height,
                    width=width / img_width,
                    height=height / img_height,
                ),
            )
        )

    return results
