"""评估、解释与回放：规则版本化，事件留痕，可按时间段回放对比。"""
import copy
import time

from .rules import RuleConflictError, condition_text, match_condition, validate_rules

_DEFAULT_RULES = (
    {"id": "r1", "priority": 100,
     "condition": {"field": "amount", "op": ">=", "value": 1000}, "action": "deny"},
    {"id": "r2", "priority": 100,
     "condition": {"field": "country", "op": "==", "value": "RU"}, "action": "deny"},
    {"id": "r3", "priority": 10,
     "condition": {"field": "channel", "op": "==", "value": "web"}, "action": "review"},
)

_VERSIONS = []   # [{"version", "rules", "note", "created_at"}]，旧版本保留用于对比回放
_EVENTS = {}     # event_id -> {"event": 完整事件, "ts": 时间戳}
_DECISIONS = {}  # event_id -> evaluate 结论
_REPLAYS = []    # 回放运行记录，供 explain 关联


def reset():
    """清空事件与回放记录，恢复默认规则集。"""
    del _VERSIONS[:]
    _EVENTS.clear()
    _DECISIONS.clear()
    del _REPLAYS[:]
    _activate(copy.deepcopy(list(_DEFAULT_RULES)), note="默认规则")
    return {"events": 0, "versions": len(_VERSIONS)}


def load_rules(rules, note="", force=False):
    """把规则集加载为新版本；存在优先级冲突时拒绝并抛出 RuleConflictError。

    force=True 可带冲突强制上线（告警照常返回）。
    """
    warnings = validate_rules(rules)
    conflicts = [w for w in warnings if w["type"] == "priority_conflict"]
    if conflicts and not force:
        raise RuleConflictError(conflicts)
    version = _activate(copy.deepcopy(list(rules)), note=note)
    return {"version": version, "warnings": warnings}


def list_versions():
    """列出全部规则版本，旧版本保留用于对比回放。"""
    return [{"version": v["version"], "note": v["note"],
             "created_at": v["created_at"],
             "rules": [r["id"] for r in v["rules"]]} for v in _VERSIONS]


def get_rules(version=None):
    """取某个版本的规则配置（深拷贝），默认当前版本。"""
    return copy.deepcopy(_get_version(version)["rules"])


def evaluate(event):
    """评估事件：返回结论、全部命中规则及取值，多条命中按优先级取结论。"""
    if not isinstance(event, dict):
        raise ValueError("event must be a dict")
    event_id = event.get("id", "")
    ts = event.get("ts", time.time())
    ruleset = _VERSIONS[-1]
    result = _run(ruleset, event)
    values = {}
    for rule in ruleset["rules"]:
        field = rule["condition"].get("field", "")
        values[field] = event.get(field)
    record = {"event": event_id, "action": result["action"], "hits": result["hits"],
              "values": values, "version": ruleset["version"], "ts": ts}
    _EVENTS[event_id] = {"event": dict(event), "ts": ts}
    _DECISIONS[event_id] = record
    return {k: record[k] for k in ("event", "action", "hits", "values", "version")}


def explain(event_id):
    """按事件查看：结论、命中规则、完整取值，以及覆盖该事件的回放结果。"""
    decision = _DECISIONS.get(event_id)
    if decision is None:
        return {"event": event_id, "found": False, "action": "",
                "hits": [], "values": {}, "replay": []}
    stored = _EVENTS.get(event_id, {})
    replays = []
    for run in _REPLAYS:
        r = run["results"].get(event_id)
        if r is not None:
            replays.append({
                "replay": run["id"], "version": run["version"],
                "action": r["action"],
                "changed": r["action"] != decision["action"],
                "hits": [h["rule"] for h in r["hits"]],
            })
    return {
        "event": event_id, "found": True,
        "action": decision["action"], "hits": decision["hits"],
        "values": dict(stored.get("event", {})),  # 完整取值，供复核
        "version": decision["version"], "ts": decision["ts"],
        "replay": replays,
    }


def replay(start=None, end=None, version=None, compare_to=None):
    """回放 [start, end) 时间段内的事件，给出命中分布与通过率。

    version 默认当前版本；compare_to 指定对比版本时，输出两个版本的
    通过率与结论变化明细，用于阈值调整前后的对比。
    """
    ruleset = _get_version(version)
    selected = [(eid, s) for eid, s in _EVENTS.items()
                if (start is None or s["ts"] >= start)
                and (end is None or s["ts"] < end)]
    results = {eid: _run(ruleset, s["event"]) for eid, s in selected}
    run = {"id": len(_REPLAYS) + 1, "version": ruleset["version"],
           "range": {"start": start, "end": end}, "results": results}
    _REPLAYS.append(run)
    summary = _summarize(results)
    changed = [
        {"event": eid, "before": _DECISIONS[eid]["action"], "after": r["action"]}
        for eid, r in results.items()
        if eid in _DECISIONS and _DECISIONS[eid]["action"] != r["action"]
    ]
    out = {"id": run["id"], "version": ruleset["version"], "range": run["range"],
           "total": len(results), "actions": summary["actions"],
           "pass_rate": summary["pass_rate"], "hits": summary["hits"],
           "changed": changed, "compare": None}
    if compare_to is not None:
        other = _get_version(compare_to)
        other_results = {eid: _run(other, s["event"]) for eid, s in selected}
        osummary = _summarize(other_results)
        diff = [{"event": eid, "before": other_results[eid]["action"],
                 "after": results[eid]["action"]}
                for eid in results
                if results[eid]["action"] != other_results[eid]["action"]]
        out["compare"] = {"version": other["version"],
                          "pass_rate": osummary["pass_rate"],
                          "actions": osummary["actions"],
                          "hits": osummary["hits"], "diff": diff}
    return out


def _activate(rules, note=""):
    _VERSIONS.append({"version": len(_VERSIONS) + 1, "rules": rules,
                      "note": note, "created_at": time.time()})
    return len(_VERSIONS)


def _get_version(version):
    if version is None:
        return _VERSIONS[-1]
    for v in _VERSIONS:
        if v["version"] == version:
            return v
    raise ValueError("unknown rules version: %s" % (version,))


def _run(ruleset, event):
    """用指定规则集评估单个事件，返回结论与命中明细（不写存储）。"""
    hits = []
    for rule in ruleset["rules"]:
        cond = rule["condition"]
        hit, actual = match_condition(cond, event)
        if hit:
            hits.append({"rule": rule["id"], "priority": rule.get("priority", 0),
                         "action": rule["action"], "condition": condition_text(cond),
                         "values": {cond.get("field", ""): actual}})
    hits.sort(key=lambda h: (-h["priority"], h["rule"]))
    return {"action": hits[0]["action"] if hits else "allow", "hits": hits}


def _summarize(results):
    actions = {}
    hits = {}
    for r in results.values():
        actions[r["action"]] = actions.get(r["action"], 0) + 1
        for h in r["hits"]:
            hits[h["rule"]] = hits.get(h["rule"], 0) + 1
    total = len(results)
    return {"actions": actions, "hits": hits,
            "pass_rate": (actions.get("allow", 0) / total) if total else None}


reset()
