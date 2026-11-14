"""按站点维护的 JSON 结构指纹巡检。

每个站点独立维护最近窗口的结构基线，对字段增删、类型漂移和
缺失率突升分别告警；冷启动样本不足时只学习不告警，异常需连续
多批出现才升级为 critical。
"""

from collections import Counter, deque

# 冷启动至少学习多少批样本，期间只学习不告警
MIN_SAMPLES = 3
# 最近窗口大小：基线与缺失率都只看最近的窗口批次
WINDOW_SIZE = 20
# 缺失率突升阈值：近期缺失率比基线高出该比例即告警
MISSING_RATE_DELTA = 0.3
# 基线中出现比例达到该值的路径才算稳定字段，反之视为可选字段
RELIABLE_PRESENCE = 0.8
# 连续多少批异常后升级为 critical
ESCALATE_BATCHES = 3


def _value_type(value):
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if value is None:
        return "null"
    return type(value).__name__


def _preview(value):
    text_value = repr(value)
    if len(text_value) > 60:
        return text_value[:57] + "..."
    return text_value


def _walk(prefix, value, nodes):
    value_type = _value_type(value)
    node = nodes.setdefault(prefix, {"types": set(), "lengths": []})
    node["types"].add(value_type)
    if value_type == "list":
        node["lengths"].append(len(value))
        node["preview"] = "list(len=%d)" % len(value)
        for index, item in enumerate(value):
            _walk("%s[%d]" % (prefix, index), item, nodes)
    elif value_type == "dict":
        node["preview"] = "dict(keys=%d)" % len(value)
        for key in sorted(value):
            _walk("%s.%s" % (prefix, key), value[key], nodes)
    else:
        node["preview"] = _preview(value)


def fingerprint(payload):
    """递归提取 payload 的结构指纹：路径 -> 类型集合与数组长度分布。"""
    nodes = {}
    _walk("$", payload, nodes)
    result = {}
    for path, node in sorted(nodes.items()):
        result[path] = {
            "types": sorted(node["types"]),
            "lengths": sorted(node["lengths"]),
            "preview": node["preview"],
        }
    return result


def _length_distribution(lengths):
    counter = Counter(lengths)
    return {
        "histogram": dict(sorted(counter.items())),
        "min": min(lengths),
        "max": max(lengths),
        "avg": round(sum(lengths) / len(lengths), 2),
    }


def _build_baseline(records):
    sample_count = len(records)
    paths = set()
    for record in records:
        paths.update(record)
    baseline = {}
    for path in paths:
        present = [record[path] for record in records if path in record]
        presence_rate = len(present) / sample_count
        entry = {
            "types": sorted({typ for node in present for typ in node["types"]}),
            "presence": round(presence_rate, 2),
        }
        all_lengths = [length for node in present for length in node["lengths"]]
        if all_lengths:
            entry["lengths"] = _length_distribution(all_lengths)
        baseline[path] = entry
    return baseline


def _new_state():
    return {
        "history": deque(maxlen=WINDOW_SIZE),
        "baseline": None,
        "reference": None,
        "consecutive": 0,
        "samples": 0,
    }


# 站点 -> 巡检状态
_sites = {}
_alerts = []


def reset():
    """清空全部站点的基线与告警。"""
    _sites.clear()
    _alerts.clear()


def _missing_rate(records, path):
    if not records:
        return 0.0
    missing = sum(1 for record in records if path not in record)
    return missing / len(records)


def _diff(site, state, current, page):
    baseline = state["baseline"]
    reference = state["reference"]
    recent = list(state["history"])
    alerts = []

    for path in sorted(set(current) - set(baseline)):
        alerts.append({
            "kind": "field_added",
            "path": path,
            "baseline": None,
            "current": _current_view(current[path]),
            "sample_page": page,
        })

    for path in sorted(set(baseline) - set(current)):
        ref_presence = reference.get(path, {}).get("presence", 0.0)
        if ref_presence >= RELIABLE_PRESENCE:
            alerts.append({
                "kind": "field_removed",
                "path": path,
                "baseline": _baseline_view(baseline[path]),
                "current": None,
                "sample_page": page,
            })

    for path in sorted(set(current) & set(baseline)):
        current_types = set(current[path]["types"])
        base_types = set(baseline[path]["types"])
        new_types = current_types - base_types
        if new_types:
            alerts.append({
                "kind": "type_changed",
                "path": path,
                "baseline": _baseline_view(baseline[path]),
                "current": _current_view(current[path]),
                "sample_page": page,
            })

    for path in sorted(reference):
        if path not in current:
            ref_presence = reference[path]["presence"]
            if ref_presence < RELIABLE_PRESENCE:
                recent_rate = _missing_rate(recent, path)
                base_missing = 1.0 - ref_presence
                if recent_rate - base_missing >= MISSING_RATE_DELTA:
                    alerts.append({
                        "kind": "missing_rate_spike",
                        "path": path,
                        "baseline": {
                            "missing_rate": round(base_missing, 2),
                            "presence": ref_presence,
                        },
                        "current": {
                            "missing_rate": round(recent_rate, 2),
                            "sample": "<missing>",
                        },
                        "sample_page": page,
                    })

    for alert in alerts:
        alert["site"] = site
    return alerts


def _baseline_view(entry):
    view = {"types": entry["types"], "presence": entry["presence"]}
    if "lengths" in entry:
        view["lengths"] = entry["lengths"]
    return view


def _current_view(node):
    view = {"types": node["types"], "sample": node["preview"]}
    if node["lengths"]:
        view["lengths"] = _length_distribution(node["lengths"])
    return view


def observe(site, payload):
    """上报一个站点的一条响应，返回本批是否告警及细节。

    冷启动阶段（少于 MIN_SAMPLES 批）只学习不告警；基线建立后，
    连续异常批次达到 ESCALATE_BATCHES 时告警级别升级为 critical。
    """
    state = _sites.setdefault(site, _new_state())
    state["samples"] += 1
    page = state["samples"]
    current = fingerprint(payload)

    if state["baseline"] is None:
        state["history"].append(current)
        if len(state["history"]) >= MIN_SAMPLES:
            state["baseline"] = _build_baseline(list(state["history"]))
            state["reference"] = {
                path: dict(entry) for path, entry in state["baseline"].items()
            }
        return {
            "site": site,
            "page": page,
            "alert": False,
            "severity": None,
            "alerts": [],
        }

    batch_alerts = _diff(site, state, current, page)
    if batch_alerts:
        state["consecutive"] += 1
        severity = "critical" if state["consecutive"] >= ESCALATE_BATCHES else "warning"
        for alert in batch_alerts:
            alert["severity"] = severity
            alert["consecutive"] = state["consecutive"]
            _alerts.append(alert)
        state["history"].append(current)
        return {
            "site": site,
            "page": page,
            "alert": True,
            "severity": severity,
            "alerts": batch_alerts,
        }

    state["consecutive"] = 0
    state["history"].append(current)
    state["baseline"] = _build_baseline(list(state["history"]))
    return {
        "site": site,
        "page": page,
        "alert": False,
        "severity": None,
        "alerts": [],
    }


def report(site=None):
    """返回累计告警；传入 site 时只返回该站点的告警。"""
    if site is None:
        return {"alerts": list(_alerts)}
    return {"alerts": [alert for alert in _alerts if alert["site"] == site]}
