"""只看原样回显的反射检测。"""

_PAGES = {}
_LAST = {"findings": []}

PAYLOAD = "<script>alert(1)</script>"


def load(page, body):
    """登记页面正文。"""
    if not isinstance(page, str) or not page:
        raise ValueError("page 不能为空")
    if not isinstance(body, str):
        raise ValueError("body 必须是字符串")
    _PAGES[page] = body
    return {"page": page, "size": len(body)}


def check(page, param):
    """原文出现几次就记几条，位置和上下文都不判断。"""
    body = _PAGES.get(page)
    if body is None:
        raise ValueError("页面未载入")
    if not isinstance(param, str) or not param:
        raise ValueError("参数名不能为空")
    findings = []
    start = 0
    while True:
        offset = body.find(PAYLOAD, start)
        if offset < 0:
            break
        findings.append({
            "param": param,
            "payload": PAYLOAD,
            "offset": offset,
            "context": "unknown",
            "decoded": "",
        })
        start = offset + 1
    _LAST["findings"] = findings
    return {
        "page": page,
        "param": param,
        "findings": [dict(item) for item in findings],
        "uncovered": [],
        "parse_errors": [],
    }


def report():
    """按页面汇总命中条数。"""
    pages = {}
    for item in _LAST["findings"]:
        pages[item["param"]] = pages.get(item["param"], 0) + 1
    return {
        "hits": len(_LAST["findings"]),
        "params": pages,
        "contexts": {},
        "uncovered": 0,
        "parse_errors": 0,
    }
