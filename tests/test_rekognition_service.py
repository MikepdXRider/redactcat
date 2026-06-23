import pytest
from botocore.stub import Stubber

from app.services.rekognition import _rekognition, detect_faces


def _face_detail(confidence: float, left: float, top: float, width: float, height: float) -> dict:
    return {
        "BoundingBox": {"Left": left, "Top": top, "Width": width, "Height": height},
        "Confidence": confidence,
    }


def test_detect_faces_maps_response() -> None:
    image = b"fake-image-bytes"
    with Stubber(_rekognition) as stubber:
        stubber.add_response(
            "detect_faces",
            {"FaceDetails": [_face_detail(98.5, 0.1, 0.2, 0.15, 0.20)]},
            {"Image": {"Bytes": image}, "Attributes": ["DEFAULT"]},
        )
        faces = detect_faces(image)

    assert len(faces) == 1
    # Confidence is normalized from 0–100 to 0–1
    assert pytest.approx(faces[0].confidence) == 0.985
    assert faces[0].bbox.left == 0.1
    assert faces[0].bbox.top == 0.2
    assert faces[0].bbox.width == 0.15
    assert faces[0].bbox.height == 0.20


def test_detect_faces_empty_response() -> None:
    image = b"blank-image"
    with Stubber(_rekognition) as stubber:
        stubber.add_response(
            "detect_faces",
            {"FaceDetails": []},
            {"Image": {"Bytes": image}, "Attributes": ["DEFAULT"]},
        )
        faces = detect_faces(image)

    assert faces == []


def test_detect_faces_multiple_faces() -> None:
    image = b"crowd-image"
    with Stubber(_rekognition) as stubber:
        stubber.add_response(
            "detect_faces",
            {
                "FaceDetails": [
                    _face_detail(95.0, 0.10, 0.10, 0.20, 0.30),
                    _face_detail(88.0, 0.60, 0.20, 0.15, 0.25),
                ]
            },
            {"Image": {"Bytes": image}, "Attributes": ["DEFAULT"]},
        )
        faces = detect_faces(image)

    assert len(faces) == 2
    assert pytest.approx(faces[0].confidence) == 0.95
    assert faces[0].bbox.left == 0.10
    assert pytest.approx(faces[1].confidence) == 0.88
    assert faces[1].bbox.left == 0.60
