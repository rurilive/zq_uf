"""按站点分别落盘，支持只写入变化条目的增量刷新。"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from .priority import build_sites, site_of
from .robots import apply_robots, build_robots

PERSISTED_FIELDS = ("loc", "lastmod", "changefreq", "score", "priority", "status")


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def site_filename(site):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", site).strip("_.")
    return f"{safe or 'unknown'}.json"


def _signature(entry):
    return entry.get("lastmod", ""), entry.get("changefreq", "")


def _persist_view(entry):
    return {field: entry.get(field) for field in PERSISTED_FIELDS}


def _load_payload(path):
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def refresh_site(out_dir, site, entries, excluded=None):
    """刷新单站点文件；没有任何变化时不重写文件。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, site_filename(site))
    payload = _load_payload(path) or {}
    old_entries = payload.get("entries", []) if isinstance(
        payload.get("entries"), list
    ) else []
    old_by_loc = {item.get("loc"): item for item in old_entries if item.get("loc")}
    current_locs = {entry["loc"] for entry in entries}

    merged = []
    changed = False
    for entry in entries:
        old = old_by_loc.get(entry["loc"])
        if (
            old is not None
            and _signature(old) == _signature(entry)
            and old.get("status") in ("active", "new")
        ):
            item = dict(old)
            item["status"] = "active"
        else:
            item = _persist_view(entry)
            item["status"] = "new" if old is None else "updated"
            changed = True
        merged.append(item)

    for old in old_entries:
        loc = old.get("loc")
        if loc and loc not in current_locs:
            item = dict(old)
            if old.get("status") != "stale":
                item["status"] = "stale"
                changed = True
            merged.append(item)

    excluded_view = [
        {"loc": item["loc"], "reason": item.get("reason", "robots Disallow")}
        for item in (excluded or [])
    ]
    if excluded_view != payload.get("excluded", []):
        changed = True

    result = {
        "site": site,
        "refreshed_at": _utc_now_iso(),
        "count": len(merged),
        "entries": merged,
        "excluded": excluded_view,
    }
    if not changed and payload:
        return {"path": path, "written": False, "changed": 0, "total": len(merged)}

    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_path, path)
    return {"path": path, "written": True, "changed": sum(
        1 for item in merged if item.get("status") in ("new", "updated", "stale")
    ), "total": len(merged)}


def refresh_all(entries, out_dir, *, robots_rules=None, auto_discover=False, now=None):
    """去重排序、应用 robots、按站点增量落盘的完整流程。"""
    parsers = build_robots(robots_rules, auto_discover=auto_discover)
    allowed, excluded = apply_robots(entries, parsers)
    sites = build_sites(allowed, now=now)
    excluded_by_site = {}
    for item in excluded:
        excluded_by_site.setdefault(site_of(item["loc"]), []).append(item)

    reports = {}
    for site, site_entries in sites.items():
        reports[site] = refresh_site(
            out_dir, site, site_entries, excluded_by_site.get(site, [])
        )
    for site, items in excluded_by_site.items():
        if site not in reports:
            reports[site] = refresh_site(out_dir, site, [], items)
    return {"reports": reports, "excluded": excluded}
