"""认证绕过矩阵：按「接口 × 身份」记录组合结果。

四种身份各自留结论，互不覆盖（同一接口的四种身份各占一格）：

    anonymous      无凭证
    expired        过期凭证
    other_user     他人凭证
    low_privilege  低权限凭证

每个格子的落地状态只有三种：

    pass   实际结果符合预期（在认证绕过核查中即被拒绝）
    fail   实际结果违背预期（发生了绕过）
    skip   登记了但没测到，带原因；与 pass/fail 严格区分

没登记过的格子为 missing。同一接口可分多次登记，每次只更新本次给出的
身份；一次「没测到」不会抹掉此前已有的结论。

``matrix(api)`` 返回该接口的覆盖汇总；``report()`` 按接口汇总全部接口，
给出覆盖组合、未通过组合、跳过原因、缺失组合、回归差异和下一轮补跑清单。
回归差异以「上一次 report() 时的结论」为基线，分 new / fixed / regressed
三类统计，因此连续两次无变化的 report() 差异为空。
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

_IDENTITIES = ("anonymous", "expired", "other_user", "low_privilege")
_VERDICTS = ("pass", "fail")

_ALLOW = {"allow", "allowed", "permit", "permitted", "accessible", "bypass", "open", "true"}
_DENY = {"deny", "denied", "block", "blocked", "forbidden", "reject", "rejected", "closed", "false"}
_PASS = {"pass", "passed", "ok"}
_FAIL = {"fail", "failed", "failure"}
_SKIP = {"skip", "skipped", "untested", "not_tested", "not-tested", "pending", "todo"}

# api -> record。结果按身份合并，任何一次调用都不会抹掉别的身份。
_APIS = {}
# (api, identity) -> pass/fail，上一次 report() 时的结论，作为回归基线。
_BASELINE = {}


class ExpiredCredentialError(ValueError):
    """无法按真实有效期生成过期凭证。"""


def reset():
    """清空接口记录与回归基线。"""
    _APIS.clear()
    _BASELINE.clear()
    return {"apis": 0}


def expired_credential(validity, issued_at=None, now=None, margin=None):
    """按凭证的真实有效期生成一份「确实已过期」的凭证时间线。

    ``validity`` 是凭证的实际有效期（秒数或 ``timedelta``）。返回签发、
    过期与核查时间，且保证 ``expires_at`` 早于 ``checked_at``；若调用方
    给出的 ``now`` 尚未越过有效期，会按实际有效期把核查时间后推，而不是
    伪造一个未过期的凭证。
    """
    seconds = _seconds(validity, "validity")
    if seconds <= 0:
        raise ExpiredCredentialError("validity must be positive")

    checked = _to_datetime(now, datetime.now(timezone.utc))
    gap = _seconds(margin, "margin") if margin is not None else max(1.0, seconds * 0.5)

    if issued_at is None:
        issued = checked - timedelta(seconds=seconds + gap)
    else:
        issued = _to_datetime(issued_at, checked)
        expires = issued + timedelta(seconds=seconds)
        if expires >= checked:
            checked = expires + timedelta(seconds=gap)

    expires = issued + timedelta(seconds=seconds)
    return {
        "validity_seconds": _tidy(seconds),
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
        "checked_at": checked.isoformat(),
        "expired_for_seconds": _tidy((checked - expires).total_seconds()),
        "expired": True,
    }


# 语义化别名。
expired_case = expired_credential


def matrix(api):
    """登记一个接口的身份组合结果，返回该接口的覆盖汇总。

    ``api`` 至少包含接口名，组合结果支持三种写法（可混用）：

        matrix({"api": "/admin/users",
                "cases": {"anonymous": "deny", "expired": "deny",
                          "other_user": "allow",
                          "low_privilege": {"status": "skip",
                                            "reason": "no account"}}})

        matrix({"api": "/admin/users", "identity": "expired",
                "actual": "deny", "validity": 900})

        matrix({"api": "/admin/users", "expected": "deny",
                "anonymous": "deny",
                "expired": {"actual": "deny", "validity": 900}})

    每个 case 可以是 allow/deny 字符串、True/False、``{"actual": ...}``、
    ``{"status": "skip", "reason": ...}``。``expected`` 默认 deny（认证
    绕过核查期望被拒绝），可整体或逐组合覆盖。``expired`` 组合要给出结论
    时必须提供 ``validity``/``ttl``（或直接给 ``expiry``），否则无法确认
    被拒绝的是真过期凭证而非有效凭证。
    """
    if not isinstance(api, dict):
        raise ValueError("api is required")
    name = api.get("api")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("api is required")
    name = name.strip()

    expected = _normalise_expected(api.get("expected", "deny"))
    cases = _extract_cases(api)
    default_validity = api.get("validity", api.get("ttl"))

    # 先全部解析，出错时不留下半条脏记录。
    parsed = []
    for identity, raw in cases.items():
        if identity not in _IDENTITIES:
            raise ValueError("unknown identity: %s" % identity)
        parsed.append(_parse_case(identity, raw, expected, default_validity))

    record = _APIS.get(name)
    if record is None:
        record = {"api": name, "expected": expected, "results": {}}
        _APIS[name] = record
    else:
        record["expected"] = expected

    for case in parsed:
        _apply(record, case)

    return _summary(record)


def report():
    """按接口汇总覆盖、未通过、跳过原因、缺失组合与回归差异。"""
    failed, skipped, missing, rerun = [], [], [], []
    diff = {"new": [], "fixed": [], "regressed": []}
    expired = {"rejected": [], "bypassed": [], "not_tested": []}
    per_api = {}
    combinations = 0

    for record in _APIS.values():
        summary = _summary(record)
        per_api[summary["api"]] = summary
        combinations += len(summary["covered"])

        for identity in summary["failed"]:
            result = summary["results"][identity]
            failed.append({
                "api": summary["api"],
                "identity": identity,
                "expected": result["expected"],
                "actual": result["actual"],
                "expiry": deepcopy(result["expiry"]),
            })
        for item in summary["skipped"]:
            skipped.append({
                "api": summary["api"],
                "identity": item["identity"],
                "reason": item["reason"],
            })
        for identity in summary["missing"]:
            missing.append({"api": summary["api"], "identity": identity})
        for item in summary["rerun"]:
            rerun.append({
                "api": summary["api"],
                "identity": item["identity"],
                "reason": item["reason"],
            })
        for category in ("new", "fixed", "regressed"):
            for identity in summary["diff"][category]:
                diff[category].append({"api": summary["api"], "identity": identity})

        state = summary["expired"]
        if state["status"] == "rejected":
            expired["rejected"].append({
                "api": summary["api"],
                "expiry": deepcopy(state["expiry"]),
            })
        elif state["status"] == "bypassed":
            expired["bypassed"].append({
                "api": summary["api"],
                "expiry": deepcopy(state["expiry"]),
            })
        else:
            expired["not_tested"].append({
                "api": summary["api"],
                "reason": state["reason"],
            })

    _BASELINE.clear()
    for record in _APIS.values():
        for identity, result in record["results"].items():
            if result["status"] in _VERDICTS:
                _BASELINE[(record["api"], identity)] = result["status"]

    expected_combinations = len(_APIS) * len(_IDENTITIES)
    return {
        "apis": len(_APIS),
        "identities": len(_IDENTITIES),
        "combinations": combinations,
        "expected_combinations": expected_combinations,
        "coverage": (float(combinations) / expected_combinations) if expected_combinations else 0.0,
        "failed": failed,
        "skipped": skipped,
        "missing": missing,
        "rerun": rerun,
        "diff": diff,
        "expired": expired,
        "per_api": per_api,
    }


def _extract_cases(api):
    cases = {}
    for key in ("cases", "results", "checks", "observations"):
        given = api.get(key)
        if isinstance(given, dict):
            cases.update(given)

    identities = [identity for identity in _IDENTITIES if identity in api]
    for identity in identities:
        cases.setdefault(identity, api[identity])

    if "identity" in api:
        payload = {key: api[key] for key in api if key in (
            "actual", "status", "reason", "expected", "validity", "ttl",
            "issued_at", "now", "margin", "expiry", "skipped",
        )}
        cases[api["identity"]] = payload
    elif not identities and any(key in api for key in ("actual", "status", "skipped")):
        # 兼容旧的单组合写法：只给 actual 时按无凭证组合登记。
        cases["anonymous"] = api

    return cases


def _parse_case(identity, raw, default_expected, default_validity):
    fields = raw if isinstance(raw, dict) else {"actual": raw}
    expected = _normalise_expected(fields.get("expected", default_expected))

    status = _parse_status(fields.get("status"))
    actual = _parse_actual(fields.get("actual", fields.get("outcome", fields.get("result"))))
    reason = fields.get("reason")
    expiry = _expiry_for(identity, fields, default_validity)

    if status is None and actual is not None:
        status = "pass" if actual == expected else "fail"
    elif status in _VERDICTS and actual is None:
        actual = expected if status == "pass" else _opposite(expected)
    elif status is None and actual is None:
        skipped = fields.get("skipped")
        if skipped is False:
            raise ValueError("%s case has no actual result" % identity)
        status = "skip"
        reason = reason or ("validity_unknown" if identity == "expired" and expiry is None
                            else "not_tested")

    if status == "skip":
        actual = None
        reason = reason or "not_tested"
    else:
        reason = None

    if identity == "expired" and status in _VERDICTS and expiry is None:
        raise ExpiredCredentialError(
            "expired case needs validity (or a pre-built expiry) to prove the "
            "credential was really expired"
        )

    return {
        "identity": identity,
        "status": status,
        "expected": expected,
        "actual": actual,
        "reason": reason,
        "expiry": expiry,
    }


def _expiry_for(identity, fields, default_validity):
    if identity != "expired":
        return None
    given = fields.get("expiry")
    if isinstance(given, dict):
        expiry = deepcopy(given)
        expiry.setdefault("expired", True)
        return expiry
    validity = fields.get("validity", fields.get("ttl", default_validity))
    if validity is None:
        return None
    return expired_credential(
        validity,
        issued_at=fields.get("issued_at"),
        now=fields.get("now"),
        margin=fields.get("margin"),
    )


def _apply(record, case):
    identity = case["identity"]
    prior = record["results"].get(identity)
    prior_status = prior["status"] if prior and prior["status"] in _VERDICTS else None

    if case["status"] == "skip" and prior_status is not None:
        # 已经测出结论的组合不因一次「没测到」而被抹掉。
        return
    record["results"][identity] = case


def _summary(record):
    results = record["results"]
    covered = [i for i in _IDENTITIES if _status(results.get(i)) in _VERDICTS]
    failed = [i for i in covered if results[i]["status"] == "fail"]
    skipped = [
        {"identity": i, "reason": results[i]["reason"] or "not_tested"}
        for i in _IDENTITIES
        if _status(results.get(i)) == "skip"
    ]
    missing = [i for i in _IDENTITIES if i not in results]

    rerun = [{"identity": i, "reason": "failed"} for i in failed]
    rerun += [{"identity": i["identity"], "reason": "skipped: %s" % i["reason"]}
              for i in skipped]
    rerun += [{"identity": i, "reason": "missing"} for i in missing]

    return {
        "api": record["api"],
        "expected": record["expected"],
        "identities": list(_IDENTITIES),
        "covered": covered,
        "failed": failed,
        "skipped": skipped,
        "missing": missing,
        "results": deepcopy(results),
        "diff": _diff_for(record["api"], results),
        "rerun": rerun,
        "expired": _expired_summary(results.get("expired")),
    }


def _diff_for(api, results):
    diff = {"new": [], "fixed": [], "regressed": []}
    for identity in _IDENTITIES:
        status = _status(results.get(identity))
        if status not in _VERDICTS:
            continue
        base = _BASELINE.get((api, identity))
        if base is None:
            diff["new"].append(identity)
        elif base == "fail" and status == "pass":
            diff["fixed"].append(identity)
        elif base == "pass" and status == "fail":
            diff["regressed"].append(identity)
    return diff


def _expired_summary(result):
    if result is None:
        return {"status": "not_tested", "reason": "missing",
                "rejected": False, "bypassed": False, "expiry": None}
    if result["status"] == "pass":
        return {"status": "rejected", "reason": None,
                "rejected": True, "bypassed": False, "expiry": deepcopy(result["expiry"])}
    if result["status"] == "fail":
        return {"status": "bypassed", "reason": None,
                "rejected": False, "bypassed": True, "expiry": deepcopy(result["expiry"])}
    # skip：没测到，绝不算作「过期凭证被拒绝」。
    return {"status": "not_tested", "reason": result["reason"] or "not_tested",
            "rejected": False, "bypassed": False, "expiry": deepcopy(result["expiry"])}


def _status(result):
    return result["status"] if result else None


def _parse_actual(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "allow" if value else "deny"
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _ALLOW:
            return "allow"
        if token in _DENY:
            return "deny"
    raise ValueError("unknown actual result: %r" % (value,))


def _parse_status(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _PASS:
            return "pass"
        if token in _FAIL:
            return "fail"
        if token in _SKIP:
            return "skip"
    raise ValueError("unknown status: %r" % (value,))


def _normalise_expected(value):
    if isinstance(value, bool):
        return "allow" if value else "deny"
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _ALLOW:
            return "allow"
        if token in _DENY:
            return "deny"
    raise ValueError("unknown expected result: %r" % (value,))


def _opposite(result):
    return "allow" if result == "deny" else "deny"


def _seconds(value, field):
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, bool):
        raise ValueError("%s must be a number of seconds" % field)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number of seconds" % field)


def _to_datetime(value, default):
    if value is None:
        return default
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid timestamp: %r" % (value,))
    else:
        raise ValueError("invalid timestamp: %r" % (value,))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _tidy(number):
    return int(number) if float(number).is_integer() else number
