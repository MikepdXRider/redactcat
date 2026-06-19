from app.schemas import DetectedEntity


def apply_text_redactions(text: str, entities: list[DetectedEntity], replacement: str = "[REDACTED]") -> str:
    for entity in sorted(entities, key=lambda e: e.start_offset, reverse=True):
        text = text[: entity.start_offset] + replacement + text[entity.end_offset :]
    return text
