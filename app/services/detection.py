import boto3

from app.schemas import DetectedEntity


def detect_pii_entities(text: str) -> list[DetectedEntity]:
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
