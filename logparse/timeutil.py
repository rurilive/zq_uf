"""时间戳解析：统一归一到 UTC。"""

from datetime import datetime, timezone

_PATTERNS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def to_utc(text):
    """把常见时间戳转成 UTC 的 ISO 8601 字符串；无时区按 UTC 处理。"""
    cleaned = text.strip()
    if cleaned.endswith(("Z", "z")):
        cleaned = cleaned[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        for pattern in _PATTERNS:
            try:
                dt = datetime.strptime(cleaned, pattern)
                break
            except ValueError:
                continue
    if dt is None:
        raise ValueError("unrecognized timestamp: %r" % text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
