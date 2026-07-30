import re

from backend.models import TranscriptSegment


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", text.lower(), flags=re.UNICODE)


def _remove_text_overlap(previous: str, current: str, max_tokens: int = 80) -> str:
    left, right = _tokens(previous), _tokens(current)
    limit = min(max_tokens, len(left), len(right))
    overlap = 0
    for size in range(1, limit + 1):
        if left[-size:] == right[:size]:
            overlap = size
    if not overlap:
        return current
    current_parts = re.findall(r"\w+|[^\w\s]|\s+", current, flags=re.UNICODE)
    consumed = 0
    index = 0
    while index < len(current_parts) and consumed < overlap:
        if not current_parts[index].isspace():
            consumed += 1
        index += 1
    return "".join(current_parts[index:]).lstrip()


def merge_segments(chunk_segments: list[list[TranscriptSegment]]) -> list[TranscriptSegment]:
    """Merge timestamped chunks and remove duplicate text from overlap windows."""

    merged: list[TranscriptSegment] = []
    for segments in chunk_segments:
        for segment in segments:
            if not merged:
                merged.append(segment)
                continue
            previous = merged[-1]
            text = segment.text
            if segment.start <= previous.end + 1:
                text = _remove_text_overlap(previous.text, text)
            if not text.strip():
                continue
            segment.text = text.strip()
            segment.words = [word for word in segment.words if word.end > previous.end]
            if (
                segment.start <= previous.end
                and segment.text.casefold() == previous.text.casefold()
            ):
                previous.end = max(previous.end, segment.end)
                continue
            merged.append(segment)
    for index, segment in enumerate(merged):
        segment.id = index
    return merged
