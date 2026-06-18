from __future__ import annotations


def wrap_text(text: str, max_line_chars: int) -> list[str]:
    """Greedy word-wrap `text` into lines of at most `max_line_chars` characters.

    A single word longer than the budget gets its own (over-long) line. Whitespace
    is collapsed to single spaces. Empty / whitespace-only input returns []."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= max_line_chars:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
