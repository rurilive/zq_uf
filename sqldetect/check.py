"""固定 payload 试参数，只认报错回显。"""

_TARGETS = {}
_LAST = {"findings": []}

PAYLOADS = ["'", "\"", "1' OR '1'='1"]


def load(url, responses):
    """登记接口和每个参数的响应正文。"""
    if not isinstance(url, str) or not url:
        raise ValueError("url 不能为空")
    if not isinstance(responses, dict):
        raise ValueError("responses 必须是字典")
    _TARGETS[url] = dict(responses)
    return {"url": url, "params": len(responses)}


def check(url, params):
    """每个参数按固定 payload 逐个试，命中就追加一条。"""
    table = _TARGETS.get(url)
    if table is None:
        raise ValueError("接口未载入")
    if not isinstance(params, list) or not params:
        raise ValueError("参数列表为空")
    findings = []
    for param in params:
        if not isinstance(param, str) or not param:
            raise ValueError("参数名不能为空")
        body = str(table.get(param, "")).lower()
        for payload in PAYLOADS:
            if "sql syntax" in body:
                findings.append({
                    "param": param,
                    "payload": payload,
                    "evidence": "sql syntax",
                    "kind": "error",
                    "request": "%s?%s=%s" % (url, param, payload),
                })
    _LAST["findings"] = findings
    return {
        "url": url,
        "findings": [dict(item) for item in findings],
        "covered": list(params),
        "pending": [],
        "skipped": [],
    }


def report():
    """按参数汇总，重复项照原样计数。"""
    findings = _LAST["findings"]
    params = {}
    for item in findings:
        params[item["param"]] = params.get(item["param"], 0) + 1
    return {
        "findings": len(findings),
        "params": params,
        "duplicates": len(findings) - len(params),
        "pending": 0,
        "skipped": 0,
    }
