"""密钥扫描入口：scan(paths) 与 report()。

形态识别见 detect.py；本模块负责按密钥内容合并、位置分类、
使用痕迹统计，以及白名单和处置状态等跨扫描持久状态。

paths 是 “仓库/文件” 到文件内容的映射，扫描过程不访问磁盘。
"""

import hashlib

from .detect import classify_location, scan_text

_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}

# 当前扫描结果（每次 scan 重建）
_entries = []
# 跨扫描持久状态：fingerprint -> {"whitelisted": bool,
# "status": str, "handled_by": str|None, "handle_method": str|None}
_state = {}


def _fingerprint(value):
    return "key-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _repo_of(name):
    return name.split("/", 1)[0] if "/" in name else name


def _count_other_occurrences(text, value, definition_line):
    """统计定义行之外同一值的出现次数，作为调用痕迹。"""
    lines = text.splitlines()
    count = 0
    for index, line in enumerate(lines, start=1):
        if index == definition_line:
            continue
        count += line.count(value)
    return count


def _merge(existing, finding, location):
    if _CONFIDENCE_RANK[finding["confidence"]] > _CONFIDENCE_RANK[
        existing["confidence"]
    ]:
        existing["confidence"] = finding["confidence"]
        existing["type"] = finding["type"]
    if location not in existing["locations"]:
        existing["locations"].append(location)
    if finding["context"] not in existing["contexts"]:
        existing["contexts"].append(finding["context"])


def scan(paths, whitelist=None):
    """扫描 paths，同一密钥内容合并成一条，返回条目与按仓库分组。

    whitelist 中的值（或 entry_id）本次起加入持久白名单。
    """
    global _entries
    if not isinstance(paths, dict):
        raise ValueError("paths must map repo/file to content")
    if whitelist:
        add_whitelist(whitelist)

    _entries = []
    index = {}
    repos = {}

    for name, text in paths.items():
        if not isinstance(name, str) or not name:
            raise ValueError("file name must be a non empty str")
        if not isinstance(text, str):
            raise ValueError("file content must be str")
        repo = _repo_of(name)
        repos.setdefault(repo, 0)
        repos[repo] += 1

        for finding in scan_text(text):
            value = finding["value"]
            fp = _fingerprint(value)
            record = _state.setdefault(fp, {
                "whitelisted": False,
                "status": "open",
                "handled_by": None,
                "handle_method": None,
            })
            if record["whitelisted"]:
                continue

            usage_count = _count_other_occurrences(text, value, finding["line"])
            location = {
                "file": name,
                "repo": repo,
                "line": finding["line"],
                "kind": classify_location(name),
                "keyword": finding["keyword"],
                "usage_hits": usage_count,
            }
            if fp not in index:
                index[fp] = len(_entries)
                _entries.append({
                    "entry_id": fp,
                    "value": value,
                    "masked": _mask(value),
                    "type": finding["type"],
                    "confidence": finding["confidence"],
                    "locations": [location],
                    "usage": [],
                    "contexts": [finding["context"]],
                    "in_use": False,
                    "status": record["status"],
                    "handled_by": record["handled_by"],
                    "handle_method": record["handle_method"],
                })
            else:
                _merge(_entries[index[fp]], finding, location)

    for entry in _entries:
        _finalize_usage(entry)

    return {
        "entries": _entries,
        "files": len(paths),
        "repos": repos,
        "repo_groups": group_by_repo(_entries),
        "whitelisted": [
            fp for fp, rec in _state.items() if rec["whitelisted"]
        ],
    }


def _finalize_usage(entry):
    usage = []
    in_use = False
    for loc in entry["locations"]:
        if loc["kind"] == "build":
            # 构建产物里的密钥大概率随制品发布，视为仍在使用。
            in_use = True
        for _ in range(loc["usage_hits"]):
            usage.append({
                "file": loc["file"],
                "repo": loc["repo"],
                "kind": loc["kind"],
                "evidence": "reference",
            })
    # 同一密钥在多个定义点出现，说明被复制使用。
    if len(entry["locations"]) > 1:
        in_use = True
        for loc in entry["locations"][1:]:
            usage.append({
                "file": loc["file"],
                "repo": loc["repo"],
                "line": loc["line"],
                "kind": loc["kind"],
                "evidence": "duplicate-definition",
            })
    entry["usage"] = usage
    entry["in_use"] = in_use or bool(usage)


def _mask(value):
    if value.startswith("-----BEGIN"):
        return "-----BEGIN PRIVATE KEY----- …(redacted)"
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:4] + "***" + value[-2:]


def group_by_repo(entries=None):
    """把条目按出现过的仓库分组，便于分派处理。"""
    entries = _entries if entries is None else entries
    groups = {}
    for entry in entries:
        for repo in {loc["repo"] for loc in entry["locations"]}:
            groups.setdefault(repo, []).append(entry["entry_id"])
    return groups


def add_whitelist(values):
    """把确认是误报的密钥值或 entry_id 加入白名单。"""
    if isinstance(values, str):
        values = [values]
    added = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError("whitelist value must be a non empty str")
        fp = value if value in _state else _fingerprint(value)
        record = _state.setdefault(fp, {
            "whitelisted": False,
            "status": "open",
            "handled_by": None,
            "handle_method": None,
        })
        if not record["whitelisted"]:
            record["whitelisted"] = True
            added.append(fp)
    return added


def resolve(entry_id, handled_by, method):
    """记录一条密钥的处置人和处置方式。"""
    if not handled_by or not method:
        raise ValueError("handled_by and method are required")
    record = _state.get(entry_id)
    if record is None:
        raise KeyError("unknown entry_id: %s" % entry_id)
    record["status"] = "resolved"
    record["handled_by"] = handled_by
    record["handle_method"] = method
    for entry in _entries:
        if entry["entry_id"] == entry_id:
            entry["status"] = "resolved"
            entry["handled_by"] = handled_by
            entry["handle_method"] = method
    return entry_id


def report():
    """按仓库汇总当前扫描结果，标出重复、使用中与已处置条目。"""
    groups = group_by_repo(_entries)
    per_repo = {
        repo: {
            "entries": sorted(ids),
            "open": sum(
                1 for e in _entries
                if e["entry_id"] in ids and e["status"] == "open"
            ),
            "in_use": sum(
                1 for e in _entries
                if e["entry_id"] in ids and e["in_use"]
            ),
        }
        for repo, ids in groups.items()
    }
    return {
        "total": len(_entries),
        "open": sum(1 for e in _entries if e["status"] == "open"),
        "resolved": sum(1 for e in _entries if e["status"] == "resolved"),
        "in_use": sum(1 for e in _entries if e["in_use"]),
        "whitelisted": sum(1 for rec in _state.values() if rec["whitelisted"]),
        "duplicates": sum(1 for e in _entries if len(e["locations"]) > 1),
        "repos": {repo: len(ids) for repo, ids in groups.items()},
        "repo_groups": per_repo,
    }


def reset():
    """清空扫描结果和持久状态，主要给测试用。"""
    global _entries, _state
    _entries = []
    _state = {}
