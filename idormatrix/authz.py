"""越权验证：按接口 × 角色组合验证，结论分允许/拒绝/异常，保留历史供回归。

api 是内存里的接口描述，cases 里放每次请求的身份、资源归属和状态码。
每个接口覆盖四种身份组合：本人(self)、他人(other)、低权限(low)、无权限(none)。
组合结论分三类：允许(allowed)、拒绝(denied)、异常(error)，异常单列，不算拒绝。
"""

ROLES = ("self", "other", "low", "none")

ALLOWED = "allowed"
DENIED = "denied"
ERROR = "error"

_ANONYMOUS = (None, "", "guest", "anonymous")

_history = {}  # name -> [run, ...]，同一接口的历史结论，run 是 {role: conclusion}
_last = {}  # name -> 最近一次 verify 的结构化结果


def _role_of(case, api):
    """确定组合角色：case 可显式给 role，否则按身份和资源归属推导。"""
    role = case.get("role")
    if role is not None:
        if role not in ROLES:
            raise ValueError("unknown role: %r" % (role,))
        return role
    actor = case.get("as", "anonymous")
    owner = case.get("resource_owner", api.get("owner"))
    if owner is not None and actor == owner:
        return "self"
    if actor in _ANONYMOUS:
        return "none"
    if actor in api.get("low_privilege", ()):
        return "low"
    return "other"


def _conclusion(status):
    """状态码归类：2xx 允许，401/403 拒绝，其余一律异常。"""
    if isinstance(status, int) and 200 <= status < 300:
        return ALLOWED
    if status in (401, 403):
        return DENIED
    return ERROR


def _judge(role, conclusion):
    """组合是否通过：异常不算通过也不算越权，单独列出。"""
    if conclusion == ERROR:
        return "error"
    if role == "self":
        return "pass" if conclusion == ALLOWED else "fail"
    return "pass" if conclusion == DENIED else "fail"


def _diff(prev, curr):
    """回归差异：对比同一接口前后两次按角色 keyed 的结论。"""
    diff = {"new": [], "changed": [], "resolved": []}
    for role, combo in curr.items():
        before = prev.get(role)
        if before is None:
            diff["new"].append(role)
            continue
        if before["conclusion"] != combo["conclusion"]:
            diff["changed"].append(
                {"role": role, "before": before["conclusion"], "after": combo["conclusion"]}
            )
        if before["result"] == "fail" and combo["result"] == "pass":
            diff["resolved"].append(role)
    return diff


def verify(api):
    """验证一个接口的角色组合覆盖，返回覆盖组合、未通过组合、身份和回归差异。"""
    if not isinstance(api, dict):
        raise ValueError("api must be a dict")
    if "name" not in api:
        raise ValueError("api needs name")
    cases = api.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")

    combos = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case must be a dict")
        status = case.get("status", 0)
        role = _role_of(case, api)
        conclusion = _conclusion(status)
        combos[role] = {  # 同一角色重复出现时以最后一次为准，方便单组合补跑
            "role": role,
            "as": case.get("as", "anonymous"),
            "resource_owner": case.get("resource_owner", api.get("owner")),
            "status": status,
            "conclusion": conclusion,
            "result": _judge(role, conclusion),
        }

    by_role = {role: {ALLOWED: 0, DENIED: 0, ERROR: 0} for role in ROLES}
    for combo in combos.values():
        by_role[combo["role"]][combo["conclusion"]] += 1

    prev = _last.get(api["name"], {}).get("combos", {})
    regression = _diff({r: c for r, c in prev.items()}, combos)

    result = {
        "api": api["name"],
        "combos": [combos[role] for role in ROLES if role in combos],
        "coverage": {
            "covered": [role for role in ROLES if role in combos],
            "missing": [role for role in ROLES if role not in combos],
        },
        "failed": [combo for combo in combos.values() if combo["result"] == "fail"],
        "errors": [combo for combo in combos.values() if combo["result"] == "error"],
        "by_role": by_role,
        "regression": regression,
    }
    _last[api["name"]] = {
        "combos": combos,
        "failed": len(result["failed"]),
        "errors": len(result["errors"]),
        "missing": result["coverage"]["missing"],
        "regression": regression,
    }
    _history.setdefault(api["name"], []).append(
        {role: combo["conclusion"] for role, combo in combos.items()}
    )
    return result


def report():
    """按接口汇总：未通过与异常组合、覆盖缺口、回归差异和历史结论。

    rerun 列出回归只需重跑的接口：最近一次未通过或有异常的，以及新增接口。
    """
    by_api = {}
    failed = 0
    errors = 0
    rerun = []
    for name, item in _last.items():
        by_api[name] = {
            "failed": item["failed"],
            "errors": item["errors"],
            "missing": item["missing"],
        }
        failed += item["failed"]
        errors += item["errors"]
        if item["failed"] or item["errors"] or len(_history.get(name, [])) <= 1:
            rerun.append(name)
    return {
        "apis": len(_last),
        "by_api": by_api,
        "failed": failed,
        "errors": errors,
        "regression": {name: item["regression"] for name, item in _last.items()},
        "history": {name: list(runs) for name, runs in _history.items()},
        "rerun": rerun,
    }
