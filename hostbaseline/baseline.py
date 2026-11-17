"""终端基线核查：按系统和版本确定适用项，支持整改复验与按终端留存。"""

import csv
from copy import deepcopy
from datetime import datetime, timezone

# 检查项登记表：适用系统、版本范围（可选）、期望值。
_CHECKS = (
    {
        "item": "firewall",
        "title": "防火墙已启用",
        "os": ("linux", "windows"),
        "expected": "on",
    },
    {
        "item": "antivirus",
        "title": "防病毒软件已启用",
        "os": ("windows",),
        "expected": "on",
    },
    {
        "item": "smb1",
        "title": "SMBv1 已禁用",
        "os": ("windows",),
        "min_version": "6.2",
        "expected": "off",
    },
    {
        "item": "ssh_root_login",
        "title": "SSH 禁止 root 直接登录",
        "os": ("linux",),
        "expected": "no",
    },
    {
        "item": "selinux",
        "title": "SELinux 处于 enforcing",
        "os": ("linux",),
        "expected": "enforcing",
    },
)

_SUPPORTED_OS = ("linux", "windows")

# host -> {"os", "version", "latest": {item: 最新结果}, "history": [每次核查]}
_HOSTS = {}


def reset():
    """清空所有终端记录。"""
    _HOSTS.clear()
    return {"hosts": 0}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _version_tuple(version):
    """把 7.9 / 10.0.19045 转成可比较的元组；无法识别时返回 None。"""
    if version is None:
        return None
    parts = str(version).strip().split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _applicability(check, os_name, version):
    """返回 (是否适用, 不适用原因)；版本未知时按适用处理，避免漏检。"""
    if os_name not in check["os"]:
        return False, "os_mismatch"
    current = _version_tuple(version)
    if current is not None:
        minimum = check.get("min_version")
        maximum = check.get("max_version")
        if minimum is not None and current < _version_tuple(minimum):
            return False, "version_out_of_range"
        if maximum is not None and current > _version_tuple(maximum):
            return False, "version_out_of_range"
    return True, None


def _snapshot(state):
    """取上一次结果中用于回溯的字段。"""
    if not state:
        return None
    return {
        "time": state.get("time"),
        "actual": state.get("actual"),
        "verdict": state.get("verdict"),
    }


def _parse_host(host):
    if not isinstance(host, dict) or not host.get("host"):
        raise ValueError("host is required")
    host_id = str(host["host"])
    os_name = str(host.get("os") or "").strip().lower()
    if not os_name:
        raise ValueError("os is required")
    if os_name not in _SUPPORTED_OS:
        raise ValueError(f"unsupported os: {os_name}")
    version = host.get("version")
    if version is not None:
        version = str(version).strip() or None
    items = host.get("items") or {}
    if not isinstance(items, dict):
        raise ValueError("items must be a dict")
    return host_id, os_name, version, items


def _evaluate(check, os_name, version, items, previous, now):
    """评估单个检查项，登记适用系统、期望值、实际值、判定和上一次结果。"""
    applicable, reason = _applicability(check, os_name, version)
    actual = items.get(check["item"])
    if not applicable:
        verdict = "not_applicable"
    else:
        verdict = "pass" if actual == check["expected"] else "fail"

    result = {
        "item": check["item"],
        "title": check["title"],
        "systems": list(check["os"]),
        "expected": check["expected"],
        "actual": actual,
        "verdict": verdict,
        "reason": reason,
        "time": now,
        "previous": _snapshot(previous),
    }
    if check.get("min_version") is not None:
        result["min_version"] = check["min_version"]
    if check.get("max_version") is not None:
        result["max_version"] = check["max_version"]

    if verdict == "fail":
        if previous and previous.get("verdict") == "fail":
            result["first_failed_at"] = (
                previous.get("first_failed_at") or previous.get("time") or now
            )
        else:
            result["first_failed_at"] = now
    return result


def _new_run(host_id, os_name, version, now, mode, scope, results, recheck):
    return {
        "host": host_id,
        "os": os_name,
        "version": version,
        "time": now,
        "mode": mode,
        "scope": list(scope),
        "passed": [r["item"] for r in results if r["verdict"] == "pass"],
        "failed": [r["item"] for r in results if r["verdict"] == "fail"],
        "not_applicable": [r["item"] for r in results if r["verdict"] == "not_applicable"],
        "results": deepcopy(results),
        "recheck": deepcopy(recheck),
    }


def check(host, recheck=False):
    """核查一台终端。

    host = {"host", "os", "version"?, "items": {检查项: 实际值}}。
    普通核查评估全部登记项，按系统/版本把不适用项单列。
    recheck=True 时只复验该终端上一次未通过的项，返回首次与本次结果对比。
    """
    host_id, os_name, version, items = _parse_host(host)
    recheck = bool(recheck or (isinstance(host, dict) and host.get("recheck")))
    now = _now()
    info = _HOSTS.get(host_id)

    if recheck:
        if info is None:
            raise ValueError(f"no previous result for host: {host_id}")
        scope = [
            spec["item"]
            for spec in _CHECKS
            if info["latest"].get(spec["item"], {}).get("verdict") == "fail"
        ]
        results = []
        comparisons = []
        for spec in _CHECKS:
            item = spec["item"]
            previous = info["latest"].get(item)
            if not previous or previous.get("verdict") != "fail":
                continue
            result = _evaluate(spec, os_name, version, items, previous, now)
            first = previous.get("first") or _snapshot(previous)
            result["first"] = deepcopy(first)
            if result["verdict"] == "pass":
                status = "fixed"
            elif result["verdict"] == "fail":
                status = "still_failing"
            else:
                status = "not_applicable"
            result["recheck_status"] = status
            results.append(result)
            comparisons.append({
                "item": item,
                "title": spec["title"],
                "expected": spec["expected"],
                "first": deepcopy(first),
                "now": {
                    "time": now,
                    "actual": result["actual"],
                    "verdict": result["verdict"],
                },
                "status": status,
            })
            info["latest"][item] = deepcopy(result)

        info["os"] = os_name
        info["version"] = version
        run = _new_run(host_id, os_name, version, now, "recheck", scope, results, comparisons)
        info["history"].append(deepcopy(run))
        return run

    if info is None:
        info = {"os": os_name, "version": version, "latest": {}, "history": []}
        _HOSTS[host_id] = info

    results = [
        _evaluate(spec, os_name, version, items, info["latest"].get(spec["item"]), now)
        for spec in _CHECKS
    ]
    for result in results:
        info["latest"][result["item"]] = deepcopy(result)
    info["os"] = os_name
    info["version"] = version
    run = _new_run(
        host_id,
        os_name,
        version,
        now,
        "check",
        [spec["item"] for spec in _CHECKS],
        results,
        [],
    )
    info["history"].append(deepcopy(run))
    return run


def _counts(states):
    """统计判定数量；不适用项不计入通过率分母。"""
    passed = sum(1 for state in states if state["verdict"] == "pass")
    failed = sum(1 for state in states if state["verdict"] == "fail")
    not_applicable = sum(1 for state in states if state["verdict"] == "not_applicable")
    denominator = passed + failed
    return {
        "items": len(states),
        "checks": denominator,
        "passed": passed,
        "failed": failed,
        "not_applicable": not_applicable,
        "rate": round(passed / denominator, 3) if denominator else 0.0,
    }


def _ordered_states(info):
    return [
        info["latest"][spec["item"]]
        for spec in _CHECKS
        if spec["item"] in info["latest"]
    ]


def _last_time(info):
    return info["history"][-1]["time"] if info["history"] else None


def _recheck_rows(host_id, info):
    """取该终端最近一次复验的对比结果；之后再做整轮核查也保留这份记录。"""
    for run in reversed(info["history"]):
        if run["mode"] != "recheck":
            continue
        return [{"host": host_id, **deepcopy(row)} for row in run["recheck"]]
    return []


def _remediation_rows(host_id, info):
    rows = []
    for state in _ordered_states(info):
        if state["verdict"] != "fail":
            continue
        rows.append({
            "host": host_id,
            "os": info["os"],
            "version": info["version"],
            "item": state["item"],
            "title": state["title"],
            "expected": state["expected"],
            "actual": state["actual"],
            "since": state.get("first_failed_at") or state["time"],
        })
    return rows


def _host_summary(host_id):
    info = _HOSTS[host_id]
    states = _ordered_states(info)
    return {
        "host": host_id,
        "os": info["os"],
        "version": info["version"],
        "time": _last_time(info),
        **_counts(states),
        "failed_items": [state["item"] for state in states if state["verdict"] == "fail"],
    }


def _by_item(host_ids):
    """按系统和检查项交叉汇总，便于分别查看 Windows/Linux 结论。"""
    grouped = {}
    for host_id in host_ids:
        info = _HOSTS[host_id]
        os_bucket = grouped.setdefault(info["os"], {})
        for state in _ordered_states(info):
            row = os_bucket.setdefault(state["item"], {
                "item": state["item"],
                "title": state["title"],
                "expected": state["expected"],
                "systems": list(state["systems"]),
                "passed": 0,
                "failed": 0,
                "not_applicable": 0,
            })
            row[{"pass": "passed", "fail": "failed", "not_applicable": "not_applicable"}[
                state["verdict"]
            ]] += 1

    order = {spec["item"]: index for index, spec in enumerate(_CHECKS)}
    return {
        os_name: sorted(rows.values(), key=lambda row: order[row["item"]])
        for os_name, rows in grouped.items()
    }


def _host_report(host_id):
    info = _HOSTS[host_id]
    states = _ordered_states(info)
    return {
        "host": host_id,
        "os": info["os"],
        "version": info["version"],
        "time": _last_time(info),
        **_counts(states),
        "passed_items": [state["item"] for state in states if state["verdict"] == "pass"],
        "failed_items": [state["item"] for state in states if state["verdict"] == "fail"],
        "not_applicable_items": [
            state["item"] for state in states if state["verdict"] == "not_applicable"
        ],
        "results": deepcopy(states),
        "recheck": _recheck_rows(host_id, info),
        "remediation": _remediation_rows(host_id, info),
        "history": deepcopy(info["history"]),
    }


def report(host=None):
    """汇总核查结果。

    report() 返回总量、按系统/检查项的结论、整改清单和复验对比。
    report(host) 返回指定终端的明细和历次核查记录。
    """
    if host is not None:
        host_id = str(host)
        if host_id not in _HOSTS:
            raise ValueError(f"unknown host: {host_id}")
        return _host_report(host_id)

    host_ids = sorted(_HOSTS)
    all_states = []
    by_os = {}
    for host_id in host_ids:
        info = _HOSTS[host_id]
        states = _ordered_states(info)
        all_states.extend(states)
        bucket = by_os.setdefault(info["os"], {"hosts": 0, "states": []})
        bucket["hosts"] += 1
        bucket["states"].extend(states)

    by_os_result = {}
    for os_name in sorted(by_os):
        bucket = by_os[os_name]
        by_os_result[os_name] = {
            "hosts": bucket["hosts"],
            **_counts(bucket["states"]),
        }

    remediation = []
    recheck = []
    for host_id in host_ids:
        info = _HOSTS[host_id]
        remediation.extend(_remediation_rows(host_id, info))
        recheck.extend(_recheck_rows(host_id, info))

    return {
        "generated_at": _now(),
        "hosts": len(host_ids),
        **_counts(all_states),
        "by_os": by_os_result,
        "by_item": _by_item(host_ids),
        "hosts_detail": [_host_summary(host_id) for host_id in host_ids],
        "remediation": remediation,
        "recheck": recheck,
    }


_REMEDIATION_FIELDS = (
    "host",
    "os",
    "version",
    "item",
    "title",
    "expected",
    "actual",
    "since",
)


def export_remediation(path, host=None):
    """把未通过项导出为整改清单 CSV。

    每行包含终端、系统/版本、检查项、期望值、实际值和起始未通过时间。
    传入 host 时只导出该终端。返回导出的行。
    """
    if host is not None:
        summary = report(host=host)
        rows = summary["remediation"]
    else:
        rows = report()["remediation"]
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=_REMEDIATION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows
