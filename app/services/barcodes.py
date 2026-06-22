"""Barcode and QR code detection via pyzbar.

Wraps pyzbar.decode() and maps the result to BarcodeDetection dataclasses
with normalized bounding boxes matching the BoundingBox schema used throughout
the PDF pipeline.

pyzbar quality is an unscaled relative integer, not a 0–1 probability, so
confidence is set to 1.0 — detection is binary (fully decoded or omitted).
Bounding boxes are derived from the polygon field for a tighter fit on
rotated symbols.
"""

from dataclasses import dataclass

import fitz
from pyzbar.pyzbar import decode

from app.schemas import BoundingBox


@dataclass
class BarcodeDetection:
    entity_type: str  # "QR_CODE" or "BARCODE"
    text: str
    bbox: BoundingBox


def detect_barcodes(pix: fitz.Pixmap) -> list[BarcodeDetection]:
    gray_pix = fitz.Pixmap(fitz.csGRAY, pix)
    codes = decode((gray_pix.samples, gray_pix.width, gray_pix.height))
    results = []
    for code in codes:
        entity_type = "QR_CODE" if code.type == "QRCODE" else "BARCODE"
        text = code.data.decode("utf-8", errors="replace")

        # default to rect 
        left = code.rect.left
        top = code.rect.top
        width = code.rect.width
        height - code.rect.height
        
        if code.polygon:
            # use polygon if available
            xs = [p.x for p in code.polygon]
            ys = [p.y for p in code.polygon]
            left=min(xs),
            top=min(ys),
            width=(max(xs) - min(xs)),
            height=(max(ys) - min(ys))

        results.append(
            BarcodeDetection(
                entity_type=entity_type,
                text=text,
                bbox=BoundingBox(
                    left=left / gray_pix.width,
                    top=top / gray_pix.height,
                    width=width / gray_pix.width,
                    height=height / gray_pix.height,
                ),
            )
        )
        
    return results
