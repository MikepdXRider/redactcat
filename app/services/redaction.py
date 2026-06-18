from app.models import JobEntity


def apply_text_redactions(text: str, entities: list[JobEntity]) -> str:
    for entity in sorted(entities, key=lambda e: e.start_offset, reverse=True):
        text = text[: entity.start_offset] + "[REDACTED]" + text[entity.end_offset :]
    return text
