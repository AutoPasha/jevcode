"""Turning a number of seconds into something a person can read."""


def human_duration(seconds):
    """`90` becomes `1m 30s`, `3700` becomes `1h 1m 40s`."""
    seconds = int(seconds)
    if seconds < 0:
        raise ValueError("duration cannot be negative")
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append("%dh" % hours)
    if minutes:
        parts.append("%dm" % minutes)
    if secs or not parts:
        parts.append("%ds" % secs)
    return " ".join(parts)
