"""认证绕过矩阵：按接口 × 身份组合记录结论，回归只补跑未通过与新增组合。"""

import time

_APIS = {}

_IDENTITIES = ("anonymous", "expired", "other_user", "low_privilege")

_PASSED = "passed"
_FAILED = "failed"
_SKIPPED = "skipped"
_UNTESTED = "untested"

_CONCLUSIVE = (_PASSED, _FAILED)


def reset():
    """清空接口记录。"""
    _APIS.clear()
    return {"apis": 0}


def _normalize(identity, entry, now):
    """把单个身份的输入规范成 {status, actual, reason}，status 为 None 表示待与 expected 比对。"""
    if isinstance(entry, str):
        entry = {"actual": entry}
    if not isinstance(entry, dict):
        raise ValueError("invalid result for identity %r" % identity)
    if entry.get("skip"):
        return {"status": _SKIPPED, "actual": None, "reason": str(entry["skip"])}
    actual = entry.get("actual")
    if actual is None:
        return {"status": _UNTESTED, "actual": None, "reason": "not tested"}
    if identity == "expired":
        expires_at = entry.get("expires_at")
        if expires_at is None:
            raise ValueError("expired identity requires expires_at")
        if expires_at >= now:
            return {
                "status": _UNTESTED,
                "actual": None,
                "reason": "credential still valid until %s" % expires_at,
            }
    return {"status": None, "actual": actual, "reason": None}


def _summary(record):
    """按接口汇总四种身份的当前结论。"""
    stored = record["identities"]
    covered, failed, skipped, untested, missing = [], [], [], [], []
    for identity in _IDENTITIES:
        entry = stored.get(identity)
        if entry is None:
            missing.append(identity)
        elif entry["status"] == _PASSED:
            covered.append(identity)
        elif entry["status"] == _FAILED:
            covered.append(identity)
            failed.append(identity)
        elif entry["status"] == _SKIPPED:
            skipped.append({"identity": identity, "reason": entry["reason"]})
        else:
            untested.append({"identity": identity, "reason": entry["reason"]})
    return {
        "api": record["api"],
        "covered": covered,
        "failed": failed,
        "skipped": skipped,
        "untested": untested,
        "missing": missing,
    }


def matrix(api):
    """登记一个接口的身份组合结果，返回该接口的组合汇总与本次回归差异。

    api 字段：
      api       接口路径（必填）
      expected  期望结果，默认 "deny"
      results   {身份: "allow"/"deny" 或 {"actual": ..., "skip": ..., "expires_at": ...}}
      actual    匿名结果的简写，等价于 results={"anonymous": ...}
      now       当前时间戳（判断凭证是否真过期），默认 time.time()
      force     True 时重跑已通过组合，默认回归只跑未通过和新增组合
    """
    if not isinstance(api, dict) or not api.get("api"):
        raise ValueError("api is required")
    name = api["api"]
    expected = api.get("expected", "deny")
    now = api.get("now", time.time())
    force = bool(api.get("force"))

    results = dict(api.get("results") or {})
    if "actual" in api and "anonymous" not in results:
        results["anonymous"] = {"actual": api["actual"]}
    unknown = sorted(set(results) - set(_IDENTITIES))
    if unknown:
        raise ValueError("unknown identities: %s" % ", ".join(unknown))

    record = _APIS.setdefault(name, {
        "api": name,
        "identities": {},
        "diff": {"new": [], "fixed": [], "regressed": []},
    })
    stored = record["identities"]

    run_diff = {"new": [], "fixed": [], "regressed": []}
    run_skipped = []

    for identity in _IDENTITIES:
        if identity not in results:
            continue
        previous = stored.get(identity)
        # 回归只跑未通过的和新增的：已通过的组合默认跳过
        if previous and previous["status"] == _PASSED and not force:
            run_skipped.append({"identity": identity, "reason": "previously passed"})
            continue
        outcome = _normalize(identity, results[identity], now)
        if outcome["status"] is None:
            outcome["status"] = _PASSED if outcome["actual"] == expected else _FAILED
        if outcome["status"] in _CONCLUSIVE:
            prev_status = previous["status"] if previous else None
            if prev_status == _FAILED and outcome["status"] == _PASSED:
                run_diff["fixed"].append(identity)
            elif prev_status == _PASSED and outcome["status"] == _FAILED:
                run_diff["regressed"].append(identity)
            elif prev_status not in _CONCLUSIVE:
                run_diff["new"].append(identity)
            stored[identity] = outcome
        else:
            # 跳过/未测到不覆盖已有结论，只在没有结论时登记原因
            if previous is None or previous["status"] not in _CONCLUSIVE:
                stored[identity] = outcome
            if outcome["status"] == _SKIPPED:
                run_skipped.append({"identity": identity, "reason": outcome["reason"]})

    for kind, identities in run_diff.items():
        record["diff"][kind].extend(i for i in identities if i not in record["diff"][kind])

    summary = _summary(record)
    seen = {item["identity"] for item in summary["skipped"]}
    summary["skipped"].extend(item for item in run_skipped if item["identity"] not in seen)
    summary["diff"] = run_diff
    return summary


def report():
    """按接口汇总组合结果，回归差异按新增、修复、回归三类统计。"""
    per_api = {}
    failed, skipped, untested = [], [], []
    diff = {"new": [], "fixed": [], "regressed": []}
    combinations = 0
    for name, record in _APIS.items():
        summary = _summary(record)
        per_api[name] = summary
        combinations += len(summary["covered"])
        failed.extend({"api": name, "identity": i} for i in summary["failed"])
        skipped.extend(dict(item, api=name) for item in summary["skipped"])
        untested.extend(dict(item, api=name) for item in summary["untested"])
        for kind in diff:
            diff[kind].extend({"api": name, "identity": i} for i in record["diff"][kind])
    return {
        "apis": len(_APIS),
        "identities": len(_IDENTITIES),
        "combinations": combinations,
        "per_api": per_api,
        "failed": failed,
        "skipped": skipped,
        "untested": untested,
        "diff": diff,
    }
