from __future__ import annotations


def count_occurrences(text: str, label: str) -> int:
    """Non-overlapping, left-to-right occurrences of `label` in `text`."""
    if not label:
        return 0
    count = 0
    start = 0
    while True:
        idx = text.find(label, start)
        if idx == -1:
            return count
        count += 1
        start = idx + len(label)
