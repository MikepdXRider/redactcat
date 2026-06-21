"""Face detection via AWS Rekognition.

Wraps detect_faces (synchronous, inline bytes) and maps the response to
FaceDetection dataclasses with normalized bounding boxes matching the
BoundingBox schema used throughout the PDF pipeline.

Rekognition confidence is 0–100; we normalize to 0–1 to match Comprehend.
"""

from dataclasses import dataclass

import boto3

from app.schemas import BoundingBox

_rekognition = boto3.client("rekognition")


@dataclass
class FaceDetection:
    confidence: float
    bbox: BoundingBox


def detect_faces(image_bytes: bytes) -> list[FaceDetection]:
    response = _rekognition.detect_faces(
        Image={"Bytes": image_bytes},
        Attributes=["DEFAULT"],
    )
    return [
        FaceDetection(
            confidence=face["Confidence"] / 100.0,
            bbox=BoundingBox(
                left=face["BoundingBox"]["Left"],
                top=face["BoundingBox"]["Top"],
                width=face["BoundingBox"]["Width"],
                height=face["BoundingBox"]["Height"],
            ),
        )
        for face in response["FaceDetails"]
    ]
