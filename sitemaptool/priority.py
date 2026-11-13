"""按站点分组、去重，并根据最后修改时间和变更频率计算优先级。"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone
from urllib.parse import urlsplit

FREQUENCY_WEIGHTS = {
    "always": 1.0,
    "hourly": 0.9,
    "daily": 0.8,
    "weekly": 0.6,
    "monthly": 0.4,
    "yearly": 0.2,
    "never": 0.05,
}
DEFAULT_WEIGHT = 0.5
DEFAULT_AGE_DAYS = 90.0
HALF_LIFE_DAYS = 180.0


def parse_lastmod(value):
    """解析 ISO 8601 lastmod，失败返回 None。"""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return calendar.timegm(parsed.astimezone(timezone.utc).utctimetuple())


def _age_days(lastmod, now):
    timestamp = parse_lastmod(lastmod)
    if timestamp is None:
        return DEFAULT_AGE_DAYS
    return max(0.0, (now - timestamp) / 86400.0)


def entry_score(entry, now=None):
    """频率分 + 老化分；长期未变更按 180 天半衰期逐步降权。"""
    now = now if now is not None else calendar.timegm(
        datetime.now(timezone.utc).utctimetuple()
    )
    weight = FREQUENCY_WEIGHTS.get(
        (entry.get("changefreq") or "").lower(), DEFAULT_WEIGHT
    )
    age = _age_days(entry.get("lastmod", ""), now)
    decay = 0.5 ** (age / HALF_LIFE_DAYS)
    return round(weight * 0.4 + decay * 0.6, 6)


def dedupe_entries(entries):
    """按 loc 去重，保留 lastmod / changefreq 信息更全的一条。"""
    merged = {}
    for entry in entries:
        loc = (entry.get("loc") or "").strip()
        if not loc:
            continue
        current = merged.get(loc)
        if current is None or _is_fresher(entry, current):
            merged[loc] = {
                "loc": loc,
                "lastmod": entry.get("lastmod", ""),
                "changefreq": entry.get("changefreq", ""),
            }
    return list(merged.values())


def _is_fresher(candidate, current):
    candidate_time = parse_lastmod(candidate.get("lastmod", "")) or 0
    current_time = parse_lastmod(current.get("lastmod", "")) or 0
    if candidate_time != current_time:
        return candidate_time > current_time
    if candidate.get("changefreq") and not current.get("changefreq"):
        return True
    return False


def site_of(loc):
    parts = urlsplit(loc)
    return parts.netloc or parts.path or "unknown"


def group_by_site(entries):
    sites = {}
    for entry in dedupe_entries(entries):
        sites.setdefault(site_of(entry["loc"]), []).append(entry)
    return sites


def rank_site(entries, now=None):
    """单站点排序：分数降序，同分时新 lastmod、高频率优先。"""
    ranked = []
    for entry in entries:
        item = dict(entry)
        item["score"] = entry_score(item, now)
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            -item["score"],
            -(parse_lastmod(item.get("lastmod", "")) or 0),
            -FREQUENCY_WEIGHTS.get(
                (item.get("changefreq") or "").lower(), DEFAULT_WEIGHT
            ),
            item["loc"],
        )
    )
    for index, item in enumerate(ranked, start=1):
        item["priority"] = index
    return ranked


def build_sites(entries, now=None):
    """去重、按站点分组并完成优先级老化排序。"""
    return {
        site: rank_site(items, now)
        for site, items in group_by_site(entries).items()
    }
