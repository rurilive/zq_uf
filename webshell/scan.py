"""webshell 扫描：内容规则 + 静态还原 + 行为信号 + 目录分组。

输入不落盘，path 是文件名到内容的映射，调用方自己决定读哪些文件。
为兼容旧调用，value 可以直接是字符串；需要带上文件创建时间和访问日志时，
value 传 {"content": "...", "created_at": "...", "access": [...]}。
"""

from datetime import datetime, timezone

from .deobfuscate import restore_source
from .grouping import group_files
from .rules import analyze_access, analyze_content, strip_php_comments

REPORT_THRESHOLD = 40
HIGH_RISK_THRESHOLD = 55
RECENT_SECONDS = 24 * 3600

_hits = []
_findings = []
_groups = []
_whitelist = set()
_scanned = 0


def _check_files(path):
    if not isinstance(path, dict):
        raise ValueError("path must map file names to content")
    normalized = {}
    for name, value in path.items():
        if not isinstance(name, str) or not name:
            raise ValueError("file name must be a non empty str")
        if isinstance(value, str):
            normalized[name] = {"content": value, "created_at": None, "access": []}
        elif isinstance(value, dict) and isinstance(value.get("content"), str):
            normalized[name] = {
                "content": value["content"],
                "created_at": value.get("created_at"),
                "access": value.get("access") or [],
            }
        else:
            raise ValueError("file content must be str or a dict with str content")
    return normalized


def _parse_created_at(value):
    """支持 Unix 时间戳和 ISO 8601，返回带时区的 datetime。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _behavior_signals(meta):
    signals = []
    created_at = _parse_created_at(meta.get("created_at"))
    if created_at is not None:
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
        if 0 <= age <= RECENT_SECONDS:
            signals.append(
                {
                    "type": "recently_created",
                    "weight": 8,
                    "detail": "created within %d hours" % (RECENT_SECONDS // 3600),
                }
            )
    signals.extend(analyze_access(meta.get("access")))
    return signals


def _build_snippet(lines, line_number):
    """命中行加上前后各一行上下文，缺行用 None 占位。"""
    context = []
    for number in range(line_number - 1, line_number + 2):
        if 1 <= number <= len(lines):
            context.append({"line": number, "text": lines[number - 1]})
        else:
            context.append({"line": number, "text": None})
    return context


def _scan_one(name, meta):
    source = meta["content"]
    original_lines = source.splitlines()
    # 还原保留注释（产物给复核同学看），规则引擎另吃一份剥注释的文本
    restored_lines, restoration = restore_source(source)
    stripped_lines = strip_php_comments(source).splitlines()
    # splitlines 对末尾换行不产生空行，长度天然与原文行列表一致
    matches, total_occurrences = analyze_content(stripped_lines, restored_lines)
    if not matches:
        return None

    content_score = sum(match["weight"] for match in matches)
    signals = _behavior_signals(meta)
    behavior_score = sum(signal["weight"] for signal in signals)
    score = content_score + behavior_score
    if score < REPORT_THRESHOLD:
        return None

    for match in matches:
        match["snippet"] = _build_snippet(original_lines, match["line"])

    return {
        "file": name,
        "score": score,
        "risk": "high" if score >= HIGH_RISK_THRESHOLD else "medium",
        "count": total_occurrences,
        "first_line": matches[0]["line"],
        "matches": matches,
        "restoration": restoration,
        "restored_preview": "\n".join(restored_lines)[:500],
        "behavior": signals,
        "group_id": None,
    }


def scan(path):
    """扫描全部文件，同一文件的多次命中合并成一条 finding。"""
    global _hits, _findings, _groups, _scanned
    files = _check_files(path)
    _scanned = len(files)
    _findings, _hits, _groups = [], [], []

    for name, meta in files.items():
        if name in _whitelist:
            continue
        finding = _scan_one(name, meta)
        if finding is not None:
            _findings.append(finding)
            # _hits 保留每条去重后的规则命中（带行号/片段），总次数另用 count 汇总
            _hits.extend(finding["matches"])

    _findings.sort(key=lambda item: (-item["score"], item["file"]))
    source_by_name = {name: meta["content"] for name, meta in files.items()}
    _groups = group_files(_findings, source_by_name)

    # 同目录成组的样本互为佐证，每组整体加权一次
    for group in _groups:
        for finding in _findings:
            if finding["group_id"] == group["id"]:
                finding["score"] += 10
                finding["risk"] = "high" if finding["score"] >= HIGH_RISK_THRESHOLD else "medium"

    return {
        "scanned": _scanned,
        "hits": sum(finding["count"] for finding in _findings),
        "files": sorted(finding["file"] for finding in _findings),
        "findings": _findings,
        "groups": _groups,
        "whitelisted": sorted(_whitelist),
    }


def report():
    """按目录给出结论，保留旧版 total/files/counts/whitelist 字段。"""
    counts = {}
    for finding in _findings:
        counts[finding["file"]] = finding["count"]

    by_directory = {}
    for finding in _findings:
        directory, _, base = finding["file"].rpartition("/")
        directory = directory or "."
        by_directory.setdefault(
            directory,
            {"files": [], "hits": 0, "high_risk": 0, "group_ids": []},
        )
        bucket = by_directory[directory]
        bucket["files"].append(
            {
                "file": base,
                "score": finding["score"],
                "risk": finding["risk"],
                "count": finding["count"],
                "first_line": finding["first_line"],
                "group_id": finding["group_id"],
            }
        )
        bucket["hits"] += finding["count"]
        if finding["risk"] == "high":
            bucket["high_risk"] += 1
        if finding["group_id"] and finding["group_id"] not in bucket["group_ids"]:
            bucket["group_ids"].append(finding["group_id"])

    for bucket in by_directory.values():
        bucket["files"].sort(key=lambda item: (-item["score"], item["file"]))

    return {
        "total": sum(counts.values()),
        "files": sorted(counts),
        "counts": counts,
        "whitelist": len(_whitelist),
        "findings": _findings,
        "groups": _groups,
        "by_directory": dict(sorted(by_directory.items())),
    }


def whitelist(name):
    """把一个文件加入跳过名单，只对该精确路径生效。"""
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non empty str")
    _whitelist.add(name)
    return {"path": name, "whitelist": len(_whitelist)}


def reset():
    """清空上一轮扫描结果和白名单，方便同一进程里跑互不干扰的多轮演示。"""
    global _hits, _findings, _groups, _scanned
    _hits, _findings, _groups, _scanned = [], [], [], 0
    _whitelist.clear()
    return {"reset": True}
