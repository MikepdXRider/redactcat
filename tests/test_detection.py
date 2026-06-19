# Unit tests for the Comprehend detection service using botocore Stubber
import boto3
from botocore.stub import Stubber
from unittest.mock import patch

from app.services.detection import detect_pii_entities


def _comprehend_response(begin: int, end: int, entity_type: str = "NAME") -> dict:
    return {
        "Entities": [
            {"Type": entity_type, "BeginOffset": begin, "EndOffset": end, "Score": 0.99}
        ]
    }


def test_detect_pii_entities_maps_response() -> None:
    client = boto3.client("comprehend", region_name="us-east-1")
    text = "John Doe lives here"
    with Stubber(client) as stubber:
        stubber.add_response(
            "detect_pii_entities",
            _comprehend_response(0, 8),
            {"Text": text, "LanguageCode": "en"},
        )
        with patch("app.services.detection.boto3.client", return_value=client):
            entities = detect_pii_entities(text)

    assert len(entities) == 1
    assert entities[0].entity_type == "NAME"
    assert entities[0].text == "John Doe"
    assert entities[0].start_offset == 0
    assert entities[0].end_offset == 8
    assert entities[0].confidence == 0.99


def test_detect_pii_entities_empty_response() -> None:
    client = boto3.client("comprehend", region_name="us-east-1")
    text = "No PII here"
    with Stubber(client) as stubber:
        stubber.add_response(
            "detect_pii_entities",
            {"Entities": []},
            {"Text": text, "LanguageCode": "en"},
        )
        with patch("app.services.detection.boto3.client", return_value=client):
            entities = detect_pii_entities(text)

    assert entities == []


def test_detect_pii_entities_multiple_types() -> None:
    client = boto3.client("comprehend", region_name="us-east-1")
    text = "Jane Smith, SSN 123-45-6789"
    with Stubber(client) as stubber:
        stubber.add_response(
            "detect_pii_entities",
            {
                "Entities": [
                    {"Type": "NAME", "BeginOffset": 0, "EndOffset": 10, "Score": 0.98},
                    {"Type": "SSN", "BeginOffset": 16, "EndOffset": 27, "Score": 0.97},
                ]
            },
            {"Text": text, "LanguageCode": "en"},
        )
        with patch("app.services.detection.boto3.client", return_value=client):
            entities = detect_pii_entities(text)

    assert len(entities) == 2
    assert entities[0].entity_type == "NAME"
    assert entities[0].text == "Jane Smith"
    assert entities[1].entity_type == "SSN"
    assert entities[1].text == "123-45-6789"
