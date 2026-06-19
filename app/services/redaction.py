from typing import Protocol


class _HasOffsets(Protocol):
    start_offset: int
    end_offset: int


def apply_text_redactions(text: str, entities: list[_HasOffsets]) -> str:
    for entity in sorted(entities, key=lambda e: e.start_offset, reverse=True):
        text = text[: entity.start_offset] + "[REDACTED]" + text[entity.end_offset :]
    return text
