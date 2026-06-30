"""Image redaction via Pillow.

Accepts raw image bytes (JPEG or PNG) and a list of entities with normalized
bounding boxes. Draws a filled black rectangle over each bbox region, then
returns the result as bytes in the original format.

Normalized bbox coordinates (0.0–1.0) are multiplied by the image pixel
dimensions at redaction time — no coordinate state is stored between scan
and redact.
"""

import io

from PIL import Image, ImageDraw

from app.schemas import ImageEntityRead


def apply_image_redactions(image_bytes: bytes, entities: list[ImageEntityRead]) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    fmt = img.format or "JPEG"
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for entity in entities:
        for bbox in entity.bboxes:
            x0 = int(bbox.left * w)
            y0 = int(bbox.top * h)
            x1 = int((bbox.left + bbox.width) * w)
            y1 = int((bbox.top + bbox.height) * h)
            draw.rectangle([x0, y0, x1, y1], fill="black")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()
