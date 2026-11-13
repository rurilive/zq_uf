"""通用表单发现：解析单页表单，并沿提交跳转追踪多步骤流程。"""

import re

DEFAULT_TYPE = "text"
HIDDEN_TYPE = "hidden"
SUBMIT_TYPES = {"submit", "button", "image", "reset"}
VALID_METHODS = {"get", "post"}
MAX_STEPS = 10

_ONETIME_RE = re.compile(r"nonce|otp|one[-_]?time", re.IGNORECASE)
_CSRF_RE = re.compile(r"csrf|xsrf|authenticity|token", re.IGNORECASE)


class FormDiscoverError(ValueError):
    """表单结构不合法时抛出，绝不静默跳过。"""


def discover(page, max_steps=MAX_STEPS):
    """发现表单流程，返回首步结果；多步骤时附带 steps 列表。"""
    if not isinstance(max_steps, int) or max_steps < 1:
        raise FormDiscoverError("max_steps 必须是正整数，得到 %r" % (max_steps,))
    steps = []
    current = page
    while current is not None:
        if len(steps) >= max_steps:
            raise FormDiscoverError("步骤数超过上限 %d，疑似循环跳转" % max_steps)
        steps.append(_parse_page(current, len(steps)))
        current = current.get("next")
    result = {key: value for key, value in steps[0].items() if key != "step"}
    if len(steps) > 1:
        result["steps"] = steps
    return result


def _parse_page(page, index):
    if not isinstance(page, dict):
        raise FormDiscoverError(
            "第 %d 步页面必须是 dict，得到 %s" % (index, type(page).__name__)
        )
    action = page.get("action")
    if not isinstance(action, str) or not action:
        raise FormDiscoverError("第 %d 步缺少有效的 action" % index)
    method = page.get("method") or "get"
    if not isinstance(method, str):
        raise FormDiscoverError("第 %d 步 method 必须是字符串" % index)
    method = method.lower()
    if method not in VALID_METHODS:
        raise FormDiscoverError(
            "第 %d 步 method=%r 不支持，仅允许 %s" % (index, method, sorted(VALID_METHODS))
        )
    inputs = page.get("inputs")
    if inputs is None:
        inputs = []
    if not isinstance(inputs, list):
        raise FormDiscoverError("第 %d 步 inputs 必须是 list" % index)
    nxt = page.get("next")
    if nxt is not None and not isinstance(nxt, dict):
        raise FormDiscoverError("第 %d 步 next 必须是 dict 或缺省" % index)

    fields, hidden, submit, tokens = [], [], [], []
    for pos, item in enumerate(inputs):
        field = _parse_input(item, index, pos)
        if field["kind"] == "submit":
            submit.append(field)
        elif field["kind"] == "hidden":
            hidden.append(field)
        else:
            fields.append(field)
        if field["token"]:
            tokens.append(field)
    return {
        "step": index,
        "action": action,
        "method": method,
        "fields": fields,
        "hidden": hidden,
        "submit": submit,
        "tokens": tokens,
    }


def _parse_input(item, step, pos):
    where = "第 %d 步第 %d 个 input" % (step, pos)
    if not isinstance(item, dict):
        raise FormDiscoverError("%s 必须是 dict，得到 %s" % (where, type(item).__name__))
    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise FormDiscoverError("%s 缺少有效的 name" % where)
    type_ = item.get("type") or DEFAULT_TYPE
    if not isinstance(type_, str):
        raise FormDiscoverError("%s 的 type 必须是字符串" % where)
    type_ = type_.lower()
    if type_ == HIDDEN_TYPE:
        kind = "hidden"
    elif type_ in SUBMIT_TYPES:
        kind = "submit"
    else:
        kind = "visible"
    token_kind = _token_kind(name)
    field = {
        "name": name,
        "type": type_,
        "kind": kind,
        "required": bool(item.get("required")),
        "token": token_kind is not None,
    }
    if token_kind is not None:
        field["token_kind"] = token_kind
    if "value" in item:
        field["value"] = item["value"]
    return field


def _token_kind(name):
    if _ONETIME_RE.search(name):
        return "onetime"
    if _CSRF_RE.search(name):
        return "csrf"
    return None
