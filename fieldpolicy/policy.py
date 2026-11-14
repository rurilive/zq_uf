"""可继承的字段脱敏策略：全局 / 域 / 表 / 字段四层，下层只能收紧不能放宽。"""

import time

MASK = "***"

_global = {"masked": False}
_domains = {}
_tables = {}
_fields = {}
_table_domains = {}
_grants = []
_audit = []


def _reset():
    _global["masked"] = False
    _domains.clear()
    _tables.clear()
    _fields.clear()
    _table_domains.clear()
    _grants.clear()
    _audit.clear()


def _domain_of(table):
    if table in _table_domains:
        return _table_domains[table]
    if "." in table:
        return table.split(".", 1)[0]
    return None


def _upper_masked(layer, table=None, domain=None):
    """本层以上各层的生效值（不含本层）。"""
    if _global["masked"]:
        return True
    if layer == "domain":
        return False
    if domain and _domains.get(domain, {}).get("masked"):
        return True
    if layer == "table":
        return False
    return _tables.get(table, {}).get("masked", False)


def _set_layer(store, key, masked, upper_masked):
    masked = bool(masked)
    if upper_masked and not masked:
        raise ValueError("下层只能收紧，不能放宽上层已脱敏的策略")
    store[key] = {"masked": masked}


def set_global(masked):
    _global["masked"] = bool(masked)


def set_domain(domain, masked):
    _set_layer(_domains, domain, masked, _upper_masked("domain"))


def set_table_domain(table, domain):
    _table_domains[table] = domain


def set_table(table, masked, domain=None):
    if domain is not None:
        _table_domains[table] = domain
    _set_layer(_tables, table, masked,
               _upper_masked("table", table=table, domain=_domain_of(table)))


def set_field(table, field, masked):
    _set_layer(_fields, (table, field), masked,
               _upper_masked("field", table=table, domain=_domain_of(table)))


def _expire(grant, now):
    if grant["active"]:
        grant["active"] = False
        _audit.append({
            "event": "exemption_expired",
            "table": grant["table"],
            "field": grant["field"],
            "until": grant["until"],
            "approver": grant["approver"],
            "expired_at": now,
        })


def _active_exemption(table, field, now):
    active = None
    for record in _grants:
        if record["table"] != table or record["field"] != field:
            continue
        if record["until"] <= now:
            _expire(record, now)
        else:
            active = {
                "table": record["table"],
                "field": record["field"],
                "until": record["until"],
                "approver": record["approver"],
            }
    return active


def resolve(table, field):
    """返回最终策略、来源层和生效豁免。"""
    domain = _domain_of(table)
    effective = False
    source_layer = "global"
    layers = [
        ("global", _global),
        ("domain", _domains.get(domain) if domain else None),
        ("table", _tables.get(table)),
        ("field", _fields.get((table, field))),
    ]
    for name, config in layers:
        if config is None:
            continue
        if config["masked"]:
            effective = True
            source_layer = name
        elif not effective:
            source_layer = name
    exemption = _active_exemption(table, field, time.time())
    return {
        "masked": effective and exemption is None,
        "source_layer": source_layer,
        "exemption": exemption,
    }


def grant(table, field, until, approver=None):
    """登记豁免：带到期时间（epoch 秒）和审批人，过期自动失效并审计。"""
    record = {
        "table": table,
        "field": field,
        "until": until,
        "approver": approver,
        "active": True,
    }
    _grants.append(record)
    now = time.time()
    if until <= now:
        _expire(record, now)
    return {
        "table": table,
        "field": field,
        "until": until,
        "approver": approver,
        "active": record["active"],
    }


def write_out(table, record):
    """写出前统一脱敏的唯一出口，不可绕过。"""
    return {
        field: MASK if resolve(table, field)["masked"] else value
        for field, value in record.items()
    }


def audit_log():
    return [dict(entry) for entry in _audit]
