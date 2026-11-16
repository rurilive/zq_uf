"""终端基线核查引擎。

check(host)          全量核查，返回通过 / 未通过 / 不适用三类明细
check(host, True)    复验，只跑该终端上一次的未通过项，与首次结果并排对比
report()             全量汇总，不适用项不计入通过率，附整改清单
report(host=...)     按终端查看，含本次与上一次结果对比
reset()              清空全部终端记录（测试用）
"""

from datetime import datetime, timezone

from .rules import RULES, applies


def _now():
    return datetime.now(timezone.utc).isoformat()


# 终端数据：{host: {"os", "os_version", "state": {item: result}, "history": [record, ...]}}
_HOSTS = {}


def reset():
    """清空终端记录。"""
    _HOSTS.clear()
    return {"hosts": 0}


def _make_result(rule, host_items, applicable):
    if not applicable:
        return {
            "item": rule["name"],
            "expected": rule["expected"],
            "actual": host_items.get(rule["name"]),
            "verdict": "not_applicable",
        }
    actual = host_items.get(rule["name"])
    return {
        "item": rule["name"],
        "expected": rule["expected"],
        "actual": actual,
        "verdict": "pass" if actual == rule["expected"] else "fail",
    }


def check(host, recheck=False):
    """核查入口。

    全量核查返回 passed / failed / not_applicable 三个明细列表，每项带
    适用系统、期望值、实际值和判定；复验只跑上次未通过项，recheck 列表里
    同时保留首次结果和复验结果以便对比。
    """
    if not isinstance(host, dict) or not host.get("host"):
        raise ValueError("host is required")

    name = host["host"]
    os_name = host.get("os", "")
    os_version = host.get("os_version")
    host_items = host.get("items") or {}

    if recheck:
        return _recheck(name, os_name, os_version, host_items)

    return _full_check(name, os_name, os_version, host_items)


def _full_check(name, os_name, os_version, host_items):
    passed, failed, not_applicable = [], [], []
    for rule in RULES:
        applicable = applies(rule, os_name, os_version)
        result = _make_result(rule, host_items, applicable)
        if result["verdict"] == "pass":
            passed.append(result)
        elif result["verdict"] == "fail":
            failed.append(result)
        else:
            not_applicable.append(result)

    record = {
        "host": name,
        "os": os_name,
        "os_version": os_version,
        "time": _now(),
        "mode": "full",
        "passed": passed,
        "failed": failed,
        "not_applicable": not_applicable,
        "recheck": [],
    }
    state = {}
    for result in passed + failed + not_applicable:
        state[result["item"]] = result
    _HOSTS[name] = {
        "os": os_name,
        "os_version": os_version,
        "state": state,
        "history": [record],
    }
    return dict(record)


def _first_result(store, item):
    """取首次全量核查中某项的原始结果。"""
    for record in store["history"]:
        if record["mode"] != "full":
            continue
        for bucket in (record["passed"], record["failed"], record["not_applicable"]):
            for result in bucket:
                if result["item"] == item:
                    return result
    return None


def _recheck(name, os_name, os_version, host_items):
    store = _HOSTS.get(name)
    if store is None:
        raise ValueError(f"host {name} has no previous check")

    if os_name:
        store["os"] = os_name
    if os_version is not None:
        store["os_version"] = os_version
    os_name = store["os"]
    os_version = store["os_version"]

    rule_map = {rule["name"]: rule for rule in RULES}
    recheck_results = []
    for item, previous in store["state"].items():
        if previous["verdict"] != "fail":
            continue
        rule = rule_map.get(item)
        applicable = rule is not None and applies(rule, os_name, os_version)
        current = _make_result(rule if rule else {"name": item, "expected": previous["expected"]},
                               host_items, applicable)
        first = _first_result(store, item) or previous
        comparison = {
            "item": item,
            "expected": current["expected"],
            "actual": current["actual"],
            "verdict": current["verdict"],
            "first_verdict": first["verdict"],
            "first_actual": first["actual"],
            "changed": first["verdict"] != current["verdict"],
        }
        recheck_results.append(comparison)
        store["state"][item] = current

    record = {
        "host": name,
        "os": os_name,
        "os_version": os_version,
        "time": _now(),
        "mode": "recheck",
        "passed": [],
        "failed": [],
        "not_applicable": [],
        "recheck": recheck_results,
    }
    store["history"].append(record)
    return dict(record)


def _host_summary(name, store):
    state = store["state"]
    passed = [r for r in state.values() if r["verdict"] == "pass"]
    failed = [r for r in state.values() if r["verdict"] == "fail"]
    not_applicable = [r for r in state.values() if r["verdict"] == "not_applicable"]
    applicable_total = len(passed) + len(failed)

    remediation = [
        {"host": name, "item": r["item"], "expected": r["expected"], "actual": r["actual"]}
        for r in failed
    ]
    history = store["history"]
    return {
        "host": name,
        "os": store["os"],
        "os_version": store["os_version"],
        "time": history[-1]["time"],
        "passed": passed,
        "failed": failed,
        "not_applicable": not_applicable,
        "recheck": history[-1]["recheck"],
        "rate": round(len(passed) / applicable_total, 3) if applicable_total else 0.0,
        "remediation": remediation,
        "previous": history[-2] if len(history) >= 2 else None,
        "history": history,
    }


def report(host=None):
    """汇总入口。

    无参数时汇总全部终端：通过率分母只计适用项（不适用项剔除），并给出
    可导出的整改清单。指定 host 时返回该终端明细及上一次核查结果。
    """
    if host is not None:
        store = _HOSTS.get(host)
        if store is None:
            raise ValueError(f"host {host} not found")
        return _host_summary(host, store)

    records = [_host_summary(name, store) for name, store in _HOSTS.items()]
    total = sum(len(r["passed"]) + len(r["failed"]) for r in records)
    passed = sum(len(r["passed"]) for r in records)
    return {
        "time": _now(),
        "hosts": len(records),
        "checks": total,
        "passed": passed,
        "rate": round(passed / total, 3) if total else 0.0,
        "not_applicable": sum(len(r["not_applicable"]) for r in records),
        "recheck": [
            {"host": r["host"], "recheck": r["recheck"]}
            for r in records if r["recheck"]
        ],
        "remediation": [item for r in records for item in r["remediation"]],
        "host_report": records,
    }
