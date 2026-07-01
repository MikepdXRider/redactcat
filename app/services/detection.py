"""PII detection service wrapping AWS Comprehend.

Called from the text, PDF, and image routers; maps the raw Comprehend response
into DetectedEntity objects with character offsets and confidence scores that
can be passed directly to the redaction service. Comprehend rejects an empty
Text argument, but PDF/image callers can legitimately have no extracted text
(e.g. a blank page or a text-less photo), so the empty-text short-circuit lives
here rather than being duplicated across every caller.
"""

import boto3

from app.schemas import DetectedEntity


def detect_pii_entities(text: str) -> list[DetectedEntity]:
    if not text:
        return []
    client = boto3.client("comprehend")
    response = client.detect_pii_entities(Text=text, LanguageCode="en")
    return [
        DetectedEntity(
            entity_type=e["Type"],
            text=text[e["BeginOffset"]: e["EndOffset"]],
            start_offset=e["BeginOffset"],
            end_offset=e["EndOffset"],
            confidence=e["Score"],
        )
        for e in response["Entities"]
    ]
