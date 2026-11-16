"""日志脱敏，当前对每个字符串字段跑一遍正则。

record 是内存里的一条日志，service 字段用来区分服务。
"""

import re

ID_CARD = re.compile(r"\b\d{17}[\dXx]\b")
CARD = re.compile(r"\b\d{16}\b")
PHONE = re.compile(r"\b1\d{10}\b")

_RULES = ((ID_CARD, "id_card"), (CARD, "card"), (PHONE, "phone"))

_masked = {}


def _mask_value(text):
    """命中哪种形态就整体替换，保留原值长度以外的东西都不留。"""
    kinds = []
    for pattern, kind in _RULES:
        if pattern.search(text):
            kinds.append(kind)
            text = pattern.sub("***", text)
    return text, kinds


def mask(record):
    """没有字段登记表，凡是像号码的都替换。"""
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    service = record.get("service", "default")
    cleaned = {}
    fields = []
    for name, value in record.items():
        if isinstance(value, str):
            new_value, kinds = _mask_value(value)
            cleaned[name] = new_value
            if kinds:
                fields.append({"field": name, "kind": kinds[0], "mode": "full"})
        else:
            cleaned[name] = value
    _masked[service] = _masked.get(service, 0) + len(fields)
    return {
        "service": service,
        "record": cleaned,
        "masked": fields,
        "unregistered": [],
        "trace_id": None,
    }


def report():
    """只按服务汇总脱敏字段数。"""
    return {
        "services": dict(_masked),
        "masked": sum(_masked.values()),
        "unregistered": 0,
        "trace_ids": 0,
    }
