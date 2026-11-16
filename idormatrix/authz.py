"""越权验证，当前逐接口按状态码给结论。

api 是内存里的接口描述，cases 里放每次请求的身份、资源归属和状态码。
"""

_last = {}


def verify(api):
    """状态码 200 算越权，其余一律算拒绝，异常也不例外。"""
    if not isinstance(api, dict):
        raise ValueError("api must be a dict")
    if "name" not in api:
        raise ValueError("api needs name")
    cases = api.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")

    checks = []
    failed = 0
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case must be a dict")
        status = case.get("status", 0)
        passed = status != 200
        if not passed:
            failed += 1
        checks.append(
            {
                "as": case.get("as", "anonymous"),
                "resource_owner": case.get("resource_owner"),
                "status": status,
                "result": "pass" if passed else "fail",
            }
        )

    _last[api["name"]] = {"checks": checks, "failed": failed}
    return {
        "api": api["name"],
        "checks": checks,
        "failed": [check for check in checks if check["result"] == "fail"],
        "coverage": len(checks),
        "regression": [],
    }


def report():
    """按接口汇总失败数，不保留上一次结果。"""
    checks = 0
    failed = 0
    apis = {}
    for name, item in _last.items():
        checks += len(item["checks"])
        failed += item["failed"]
        apis[name] = item["failed"]
    return {
        "apis": len(_last),
        "by_api": apis,
        "checks": checks,
        "failed": failed,
        "regressed": 0,
    }
