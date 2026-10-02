"""Synthetic timing rule for the context-handoff example."""
import math


def packet_span(rows):
    if not rows:
        return None
    spans = []
    for row in rows:
        pts, duration = row["pts"], row["duration"]
        if not isinstance(pts, (int, float)) or not math.isfinite(pts):
            return None
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            return None
        spans.append((pts, pts + duration))
    return max(end for _, end in spans) - min(start for start, _ in spans)


def valid_output_duration(value):
    if type(value) not in (int, float) or value <= 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def verify(source_rows, output_duration):
    source_duration = packet_span(source_rows)
    if source_duration is None or not valid_output_duration(output_duration):
        return "rejected: source or output audio timing unavailable"
    return "accepted" if output_duration >= source_duration - 0.25 else "rejected: audio shortened"
