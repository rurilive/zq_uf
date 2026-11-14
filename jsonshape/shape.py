"""按站点和路径的结构指纹巡检。

每次 observe 采集一个或一组响应页，递归提取字段路径、类型和数组长度
分布，聚合成结构指纹；每个站点在最近窗口内独立维护基线，并对字段增删、
类型变化和缺失率突升分别告警。冷启动样本不足时只学习不告警，连续多批
异常才升级为严重告警。
"""

from collections import Counter, defaultdict, deque

# 冷启动：站点累计样本不足该页数，或批次不足该数，只学习不告警。
MIN_PAGES = 5
MIN_BATCHES = 3
# 最近窗口保留的批次数。
WINDOW_BATCHES = 20
# 连续多少批异常后由 warning 升级为 critical。
ESCALATE_STREAK = 3
# 缺失率突升阈值：当前缺失率 - 基线缺失率 >= 该值，且当前缺失率 >= 下限。
MISSING_RATE_DELTA = 0.3
MISSING_RATE_MIN = 0.2
# 每条告警附带的异常样本页上限。
SAMPLE_PAGES_LIMIT = 3

_sites = {}
_alerts = []


def reset():
    """清空所有站点的基线与告警。"""
    _sites.clear()
    _alerts.clear()


def extract_fingerprint(value):
    """递归提取单个响应页的结构指纹。

    返回 {路径元组: {"type": 类型名, "array_len": 长度或None}}，
    同一路径（如数组元素）多次出现时以首次出现为准。
    """
    fingerprint = {}

    def walk(node, path):
        node_type = type(node).__name__
        if path in fingerprint:
            entry = fingerprint[path]
        else:
            entry = fingerprint[path] = {"type": node_type, "array_len": None}
        if isinstance(node, dict):
            for key in sorted(node.keys()):
                walk(node[key], path + (key,))
        elif isinstance(node, list):
            entry["array_len"] = len(node)
            for item in node:
                walk(item, path + ("[]",))

    walk(value, ("$",))
    return fingerprint


def _new_path_state():
    return {
        "types": Counter(),
        "array_lens": Counter(),
        "pages": 0,
    }


def _new_site():
    return {
        "name": None,
        "total_pages": 0,
        "batches": deque(maxlen=WINDOW_BATCHES),
        "streak": 0,
    }


def _aggregate(pages):
    """把一批响应页聚合为结构统计。"""
    paths = defaultdict(_new_path_state)
    page_fingerprints = []
    for page in pages:
        fingerprint = extract_fingerprint(page)
        page_fingerprints.append(fingerprint)
        for path, entry in fingerprint.items():
            state = paths[path]
            state["pages"] += 1
            state["types"][entry["type"]] += 1
            if entry["array_len"] is not None:
                state["array_lens"][entry["array_len"]] += 1
    return paths, page_fingerprints


def _window_stats(site):
    """汇总最近窗口内所有批次的结构统计。"""
    stats = defaultdict(_new_path_state)
    window_pages = 0
    for batch in site["batches"]:
        window_pages += batch["pages"]
        for path, state in batch["paths"].items():
            merged = stats[path]
            merged["pages"] += state["pages"]
            merged["types"].update(state["types"])
            merged["array_lens"].update(state["array_lens"])
    return stats, window_pages


def _describe(path, state, pages):
    """生成可 JSON 序列化的路径结构描述。"""
    type_counts = dict(state["types"])
    dominant_type = max(type_counts, key=type_counts.get) if type_counts else None
    return {
        "path": ".".join(path),
        "type": dominant_type,
        "types": type_counts,
        "array_len": dict(state["array_lens"]),
        "present": state["pages"],
        "missing_rate": round(1 - state["pages"] / pages, 4) if pages else 0.0,
    }


def _collect_sample_pages(kind, path, page_fingerprints, current):
    samples = []
    for index, fingerprint in enumerate(page_fingerprints):
        entry = fingerprint.get(path)
        if kind == "field_added":
            matched = entry is not None
        elif kind == "type_changed":
            matched = entry is not None and entry["type"] == current["type"]
        else:
            matched = entry is None
        if matched:
            samples.append(index)
            if len(samples) >= SAMPLE_PAGES_LIMIT:
                break
    return samples


def _detect(site, batch, window_pages):
    alerts = []
    stats, _ = _window_stats(site)
    batch_paths = batch["paths"]
    batch_pages = batch["pages"]
    page_fingerprints = batch["fingerprints"]
    all_paths = set(stats) | set(batch_paths)

    for path in sorted(all_paths, key=lambda item: ".".join(item)):
        base_state = stats.get(path)
        current_state = batch_paths.get(path)

        if base_state is None and current_state is not None:
            current = _describe(path, current_state, batch_pages)
            alerts.append({
                "site": site["name"],
                "kind": "field_added",
                "path": current["path"],
                "baseline": None,
                "current": current,
                "sample_pages": _collect_sample_pages(
                    "field_added", path, page_fingerprints, current
                ),
            })
            continue

        if base_state is not None and current_state is None:
            baseline = _describe(path, base_state, window_pages)
            alerts.append({
                "site": site["name"],
                "kind": "field_removed",
                "path": baseline["path"],
                "baseline": baseline,
                "current": None,
                "sample_pages": _collect_sample_pages(
                    "field_removed", path, page_fingerprints, {}
                ),
            })
            continue

        if base_state is None or current_state is None:
            continue

        baseline = _describe(path, base_state, window_pages)
        current = _describe(path, current_state, batch_pages)

        if current["type"] != baseline["type"]:
            alerts.append({
                "site": site["name"],
                "kind": "type_changed",
                "path": current["path"],
                "baseline": baseline,
                "current": current,
                "sample_pages": _collect_sample_pages(
                    "type_changed", path, page_fingerprints, current
                ),
            })
            continue

        missing_delta = current["missing_rate"] - baseline["missing_rate"]
        if (
            missing_delta >= MISSING_RATE_DELTA
            and current["missing_rate"] >= MISSING_RATE_MIN
        ):
            alerts.append({
                "site": site["name"],
                "kind": "missing_rate_spike",
                "path": current["path"],
                "baseline": baseline,
                "current": current,
                "sample_pages": _collect_sample_pages(
                    "missing_rate_spike", path, page_fingerprints, current
                ),
            })

    return alerts


def observe(site, payload):
    """采集一个站点的一批响应（单个 dict 或 dict 列表），返回本批告警状态。"""
    if isinstance(payload, dict):
        pages = [payload]
    elif isinstance(payload, list):
        pages = payload
    else:
        raise TypeError("payload must be a dict or a list of dicts")
    if not pages:
        return {"site": site, "alert": False, "alerts": []}

    state = _sites.get(site)
    if state is None:
        state = _new_site()
        state["name"] = site
        _sites[site] = state

    paths, fingerprints = _aggregate(pages)
    batch = {
        "pages": len(pages),
        "paths": paths,
        "fingerprints": fingerprints,
    }

    window_before = sum(item["pages"] for item in state["batches"])
    total_after = state["total_pages"] + len(pages)
    cold = (
        total_after < MIN_PAGES
        or len(state["batches"]) < MIN_BATCHES - 1
    )

    alerts = []
    if not cold:
        alerts = _detect(state, batch, window_before)

    state["batches"].append(batch)
    state["total_pages"] += len(pages)

    if alerts:
        state["streak"] += 1
        severity = "critical" if state["streak"] >= ESCALATE_STREAK else "warning"
        for alert in alerts:
            alert["severity"] = severity
            alert["streak"] = state["streak"]
            _alerts.append(alert)
    else:
        state["streak"] = 0

    return {
        "site": site,
        "alert": bool(alerts),
        "severity": (
            "critical" if alerts and state["streak"] >= ESCALATE_STREAK
            else ("warning" if alerts else "ok")
        ),
        "alerts": alerts,
        "cold_start": cold,
    }


def report(site=None):
    """返回全部站点或指定站点的告警列表。"""
    if site is None:
        return {"alerts": list(_alerts)}
    return {"alerts": [alert for alert in _alerts if alert["site"] == site]}
