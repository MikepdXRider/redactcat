import boto3

from app.models import JobEntity


def detect_pii_entities(text: str, job_id: int) -> list[JobEntity]:
    client = boto3.client("comprehend")
    response = client.detect_pii_entities(Text=text, LanguageCode="en")
    return [
        JobEntity(
            job_id=job_id,
            entity_type=e["Type"],
            text=text[e["BeginOffset"]: e["EndOffset"]],
            start_offset=e["BeginOffset"],
            end_offset=e["EndOffset"],
            confidence=e["Score"],
        )
        for e in response["Entities"]
    ]
