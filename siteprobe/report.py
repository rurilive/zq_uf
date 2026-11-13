"""结构化报告的落盘、加载与改版差异对比。"""

import json
import os

REPORT_VERSION = 1
PAGE_METRICS = ("status", "bytes", "redirect_count", "encoding", "garbled_ratio")


def save_report(report, path):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def load_report(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def diff_reports(old_report, new_report):
    """对比两份报告，忽略时间戳，输出站点改版可见的结构化差异。"""
    result = {
        "url": new_report.get("url"),
        "classification_changed": [],
        "pagination_changed": [],
        "signals_changed": [],
        "pages_changed": [],
        "pages_added": [],
        "pages_removed": [],
    }

    for field in ("classification", "score", "confidence"):
        old_value = old_report.get("assessment", {}).get(field)
        new_value = new_report.get("assessment", {}).get(field)
        if old_value != new_value:
            result["classification_changed"].append(
                {"field": field, "old": old_value, "new": new_value}
            )

    old_pagination = old_report.get("pagination", {})
    new_pagination = new_report.get("pagination", {})
    for field in ("style", "page_param", "path_pattern", "next_selector", "total_pages"):
        if old_pagination.get(field) != new_pagination.get(field):
            result["pagination_changed"].append(
                {"field": field, "old": old_pagination.get(field), "new": new_pagination.get(field)}
            )

    for group in ("login_required", "captcha", "signature"):
        old_set = set(old_report.get("signals", {}).get(group, []))
        new_set = set(new_report.get("signals", {}).get(group, []))
        for evidence in sorted(new_set - old_set):
            result["signals_changed"].append({"group": group, "change": "added", "evidence": evidence})
        for evidence in sorted(old_set - new_set):
            result["signals_changed"].append({"group": group, "change": "removed", "evidence": evidence})

    old_pages = {page["url"]: page for page in old_report.get("pages", [])}
    new_pages = {page["url"]: page for page in new_report.get("pages", [])}
    for url in sorted(new_pages.keys() - old_pages.keys()):
        result["pages_added"].append(url)
    for url in sorted(old_pages.keys() - new_pages.keys()):
        result["pages_removed"].append(url)
    for url in sorted(old_pages.keys() & new_pages.keys()):
        changes = []
        for field in PAGE_METRICS:
            if old_pages[url].get(field) != new_pages[url].get(field):
                changes.append(
                    {"field": field, "old": old_pages[url].get(field), "new": new_pages[url].get(field)}
                )
        if changes:
            result["pages_changed"].append({"url": url, "changes": changes})

    result["changed"] = any(
        result[key]
        for key in ("classification_changed", "pagination_changed", "signals_changed",
                    "pages_changed", "pages_added", "pages_removed")
    )
    return result
