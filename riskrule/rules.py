"""规则配置：条件、阈值、动作分开配置，加载前检测优先级冲突。

规则结构：
    {
        "id": "r1",
        "priority": 100,                          # 数值越大优先级越高
        "condition": {"field": "amount", "op": ">=", "value": 1000},
        "action": "deny",
    }
"""

_OPS = (">=", ">", "<=", "<", "==", "!=", "in")
_RANGE_OPS = (">=", ">", "<=", "<")


class RuleConflictError(ValueError):
    """规则集存在优先级冲突，warnings 属性携带明细。"""

    def __init__(self, warnings):
        self.warnings = list(warnings)
        super().__init__("; ".join(w["message"] for w in self.warnings))


def condition_text(cond):
    return "%s %s %s" % (cond.get("field", ""), cond.get("op", ""), cond.get("value"))


def match_condition(cond, event):
    """返回 (是否命中, 实际取值)。字段缺失或类型不符视为未命中。"""
    op = cond.get("op", "")
    expect = cond.get("value")
    actual = event.get(cond.get("field", ""))
    if op in _RANGE_OPS:
        number = isinstance(actual, (int, float)) and not isinstance(actual, bool)
        if not number or not isinstance(expect, (int, float)):
            return False, actual
        if op == ">=":
            return actual >= expect, actual
        if op == ">":
            return actual > expect, actual
        if op == "<=":
            return actual <= expect, actual
        return actual < expect, actual
    if op == "==":
        return actual == expect, actual
    if op == "!=":
        return actual != expect, actual
    if op == "in":
        return actual in (expect or []), actual
    return False, actual


def validate_rules(rules):
    """返回告警列表。优先级相同、动作不同且条件可能同时命中视为冲突。"""
    warnings = []
    seen = set()
    for rule in rules:
        rid = rule.get("id", "")
        if not rid:
            warnings.append(_warn("invalid_rule", [], "规则缺少 id"))
        elif rid in seen:
            warnings.append(_warn("duplicate_id", [rid], "规则 id 重复: %s" % rid))
        seen.add(rid)
        cond = rule.get("condition") or {}
        if cond.get("op") not in _OPS:
            warnings.append(_warn("invalid_rule", [rid],
                                  "规则 %s 的运算符不支持: %s" % (rid, cond.get("op"))))
        if not rule.get("action"):
            warnings.append(_warn("invalid_rule", [rid], "规则 %s 缺少 action" % rid))
    for i in range(len(rules)):
        for j in range(i + 1, len(rules)):
            a, b = rules[i], rules[j]
            if a.get("priority", 0) != b.get("priority", 0):
                continue
            if a.get("action") == b.get("action"):
                continue
            if not _overlap(a.get("condition") or {}, b.get("condition") or {}):
                continue
            warnings.append({
                "type": "priority_conflict",
                "rules": [a.get("id"), b.get("id")],
                "priority": a.get("priority", 0),
                "actions": [a.get("action"), b.get("action")],
                "message": "规则 %s 与 %s 优先级相同(%s)但动作不同(%s/%s)，"
                           "条件可能同时命中，执行结论不确定" % (
                               a.get("id"), b.get("id"), a.get("priority", 0),
                               a.get("action"), b.get("action")),
            })
    return warnings


def _warn(wtype, rules, message):
    return {"type": wtype, "rules": rules, "message": message}


def _overlap(c1, c2):
    """保守判断两个条件是否可能同时命中；同字段时做互斥分析。"""
    if c1.get("field") != c2.get("field"):
        return True
    o1, o2 = c1.get("op"), c2.get("op")
    v1, v2 = c1.get("value"), c2.get("value")
    if o1 == "==" and o2 == "==":
        return v1 == v2
    if o1 == "==" and o2 == "!=":
        return v1 != v2
    if o1 == "!=" and o2 == "==":
        return v1 != v2
    if o1 == "in" and o2 == "==":
        return v2 in (v1 or [])
    if o1 == "==" and o2 == "in":
        return v1 in (v2 or [])
    if o1 == "in" and o2 == "in":
        return bool(set(map(repr, v1 or [])) & set(map(repr, v2 or [])))
    i1 = _as_interval(o1, v1)
    i2 = _as_interval(o2, v2)
    if i1 is not None and i2 is not None:
        return _intervals_overlap(i1, i2)
    if o1 == "in" and i2 is not None:
        return any(_in_interval(x, i2) for x in (v1 or []))
    if o2 == "in" and i1 is not None:
        return any(_in_interval(x, i1) for x in (v2 or []))
    return True


def _as_interval(op, value):
    """把数值条件转成区间 (lo, lo_incl, hi, hi_incl)，非数值返回 None。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if op == ">=":
        return (value, True, None, False)
    if op == ">":
        return (value, False, None, False)
    if op == "<=":
        return (None, False, value, True)
    if op == "<":
        return (None, False, value, False)
    if op == "==":
        return (value, True, value, True)
    return None


def _intervals_overlap(a, b):
    if a[2] is not None and b[0] is not None:
        if a[2] < b[0] or (a[2] == b[0] and not (a[3] and b[1])):
            return False
    if b[2] is not None and a[0] is not None:
        if b[2] < a[0] or (b[2] == a[0] and not (b[3] and a[1])):
            return False
    return True


def _in_interval(x, interval):
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False
    lo, lo_incl, hi, hi_incl = interval
    if lo is not None and (x < lo or (x == lo and not lo_incl)):
        return False
    if hi is not None and (x > hi or (x == hi and not hi_incl)):
        return False
    return True
