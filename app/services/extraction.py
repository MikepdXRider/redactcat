"""Document text extraction via AWS Textract.

Accepts PDF, JPEG, or PNG via S3 object reference. Calls detect_document_text
(synchronous, single-page only) and returns the assembled text string plus per-word
character spans with normalized bounding boxes, so callers can map Comprehend
character offsets back to Textract bboxes.

Reading order is derived from the PAGE→LINE→WORD CHILD relationship chain in the
Textract response rather than block array order, which is not guaranteed to be sorted.
Words within a line are joined with spaces; lines are joined with newlines.
"""

from dataclasses import dataclass

import boto3

from app.schemas import BoundingBox

_textract = boto3.client("textract")


@dataclass
class WordSpan:
    start_char: int
    end_char: int
    left: float
    top: float
    width: float
    height: float


def bboxes_for_entity(start: int, end: int, word_spans: list[WordSpan]) -> list[BoundingBox]:
    return [
        BoundingBox(left=ws.left, top=ws.top, width=ws.width, height=ws.height)
        for ws in word_spans
        if ws.start_char < end and ws.end_char > start
    ]


def extract_text_from_s3_object(bucket: str, key: str) -> tuple[str, list[WordSpan]]:
    response = _textract.detect_document_text(
        Document={"S3Object": {"Bucket": bucket, "Name": key}}
    )

    blocks_by_id: dict[str, dict] = {b["Id"]: b for b in response["Blocks"]}

    page_block = next(b for b in response["Blocks"] if b["BlockType"] == "PAGE")
    line_ids = [
        child_id
        for rel in page_block.get("Relationships", [])
        if rel["Type"] == "CHILD"
        for child_id in rel["Ids"]
    ]

    text_parts: list[str] = []
    word_spans: list[WordSpan] = []
    pos = 0

    for i, line_id in enumerate(line_ids):
        line_block = blocks_by_id.get(line_id)
        if not line_block or line_block["BlockType"] != "LINE":
            continue

        word_ids = [
            child_id
            for rel in line_block.get("Relationships", [])
            if rel["Type"] == "CHILD"
            for child_id in rel["Ids"]
        ]

        for j, word_id in enumerate(word_ids):
            word_block = blocks_by_id.get(word_id)
            if not word_block or word_block["BlockType"] != "WORD":
                continue

            word_text: str = word_block["Text"]
            bbox = word_block["Geometry"]["BoundingBox"]
            start = pos
            end = pos + len(word_text)

            word_spans.append(
                WordSpan(
                    start_char=start,
                    end_char=end,
                    left=bbox["Left"],
                    top=bbox["Top"],
                    width=bbox["Width"],
                    height=bbox["Height"],
                )
            )
            text_parts.append(word_text)
            pos = end

            if j < len(word_ids) - 1:
                text_parts.append(" ")
                pos += 1

        if i < len(line_ids) - 1:
            text_parts.append("\n")
            pos += 1

    return "".join(text_parts), word_spans
