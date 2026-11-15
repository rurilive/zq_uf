"""采集数据落库前的字段脱敏与受控还原。

- 按字段规则脱敏，保留前后缀；未配置字段走默认策略，新增字段自动继承。
- 密钥版本化，令牌绑定密钥版本、请求人和角色。
- 还原需要有效令牌且角色具备 reveal 权限；失败告警并记录请求人。
- 规则变更与密钥轮换需审批人；全部操作写审计。
"""

import json
import logging
import time
import uuid

logger = logging.getLogger("maskctl")

MASK_CHAR = "*"

# 密钥版本表：version -> secret（纯标准库演示，生产应接 KMS）
KEYS = {"v1": "maskctl-dev-key-v1"}
ACTIVE_KEY_VERSION = "v1"

# 角色权限
ROLES = {
    "writer": {"mask"},
    "integrator": {"mask", "reveal"},
    "admin": {"mask", "reveal", "approve"},
}

# 规则/密钥变更审批人
APPROVERS = {"admin"}

# 脱敏规则：table -> field -> {"prefix": 保留前缀长度, "suffix": 保留后缀长度}
RULES = {
    "default": {
        "phone": {"prefix": 3, "suffix": 4},
        "id_card": {"prefix": 4, "suffix": 4},
        "email": {"prefix": 1, "suffix": 0},
    },
    "users": {
        "phone": {"prefix": 3, "suffix": 4},
        "address": {"prefix": 6, "suffix": 0},
    },
}

# 默认策略：未配置字段保留首 1 位，其余脱敏；新增采集字段自动继承
DEFAULT_POLICY = {"prefix": 1, "suffix": 0}

# 令牌库：token -> 原值与签发上下文（演示用内存实现，生产应落安全存储）
_VAULT = {}

# 审计日志与告警（演示用内存实现，生产应接日志/告警通道）
AUDIT_LOG = []
ALERTS = []


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _audit(op, **fields):
    entry = {"ts": _now(), "op": op}
    entry.update(fields)
    AUDIT_LOG.append(entry)
    return entry


def _alert(kind, **fields):
    alert = {"ts": _now(), "reason": kind}
    alert.update(fields)
    ALERTS.append(alert)
    logger.warning("maskctl alert: %s", alert)
    return alert


def _rule_for(table, field):
    """规则解析：表内规则 -> default 表规则 -> 默认策略。"""
    table_rules = RULES.get(table, {})
    if field in table_rules:
        return table_rules[field]
    if field in RULES.get("default", {}):
        return RULES["default"][field]
    return DEFAULT_POLICY


def _mask_value(value, rule):
    """按规则保留前后缀脱敏。返回 (新值, 是否脱敏)。

    非字符串或长度不足以保留前后缀的值原样跳过。
    """
    if not isinstance(value, str):
        return value, False
    prefix, suffix = rule["prefix"], rule["suffix"]
    if len(value) <= prefix + suffix:
        return value, False
    middle = MASK_CHAR * (len(value) - prefix - suffix)
    tail = value[-suffix:] if suffix else ""
    return value[:prefix] + middle + tail, True


def mask(row, table="default", requester="system", role="integrator"):
    """按规则脱敏一行数据，返回脱敏结果、令牌和本次审计。"""
    out = {}
    masked_fields = []
    originals = {}
    audit = []
    for field, value in row.items():
        rule = _rule_for(table, field)
        new_value, changed = _mask_value(value, rule)
        out[field] = new_value
        if changed:
            masked_fields.append(field)
            originals[field] = value
        audit.append(_audit(
            "mask", table=table, field=field, requester=requester,
            result="masked" if changed else "skipped",
        ))
    token = None
    if originals:
        token = "%s-%s" % (ACTIVE_KEY_VERSION, uuid.uuid4().hex[:12])
        _VAULT[token] = {
            "row": originals,
            "table": table,
            "requester": requester,
            "role": role,
            "key_version": ACTIVE_KEY_VERSION,
        }
    return {"row": out, "masked": masked_fields, "token": token, "audit": audit}


def reveal(row, token, requester=None):
    """凭令牌受控还原。需要令牌有效且签发角色具备 reveal 权限。"""
    audit = []
    entry = _VAULT.get(token)
    req = requester or (entry["requester"] if entry else "unknown")

    if entry is None:
        _alert("reveal_denied", token=token, requester=req, detail="invalid_token")
        audit.append(_audit("reveal", token=token, requester=req,
                            result="denied:invalid_token"))
        return {"row": dict(row), "revealed": False, "audit": audit}

    if "reveal" not in ROLES.get(entry["role"], set()):
        _alert("reveal_denied", token=token, requester=req, detail="no_permission")
        audit.append(_audit("reveal", token=token, requester=req,
                            result="denied:no_permission"))
        return {"row": dict(row), "revealed": False, "audit": audit}

    if entry["key_version"] not in KEYS:
        _alert("reveal_denied", token=token, requester=req, detail="key_retired")
        audit.append(_audit("reveal", token=token, requester=req,
                            result="denied:key_retired"))
        return {"row": dict(row), "revealed": False, "audit": audit}

    restored = dict(row)
    restored.update(entry["row"])
    audit.append(_audit("reveal", token=token, requester=req,
                        fields=sorted(entry["row"]), result="ok"))
    return {"row": restored, "revealed": True, "audit": audit}


def set_rule(table, field, rule, approver):
    """变更脱敏规则，需审批人审批，全程写审计。"""
    if approver not in APPROVERS:
        _audit("rule_change", table=table, field=field,
               approver=approver, result="rejected")
        raise PermissionError("规则变更需审批人审批，%r 不在审批人名单" % approver)
    RULES.setdefault(table, {})[field] = {
        "prefix": int(rule["prefix"]),
        "suffix": int(rule["suffix"]),
    }
    _audit("rule_change", table=table, field=field,
           approver=approver, result="approved")


def rotate_key(version, secret, approver):
    """轮换密钥版本，旧版本保留以支持历史令牌还原。"""
    if approver not in APPROVERS:
        _audit("key_rotate", version=version, approver=approver, result="rejected")
        raise PermissionError("密钥轮换需审批人审批，%r 不在审批人名单" % approver)
    global ACTIVE_KEY_VERSION
    KEYS[version] = secret
    ACTIVE_KEY_VERSION = version
    _audit("key_rotate", version=version, approver=approver, result="approved")


def export_inventory(path=None):
    """按表导出脱敏清单；给定 path 时同时写 JSON 文件。"""
    inventory = {table: {field: dict(rule) for field, rule in fields.items()}
                 for table, fields in RULES.items()}
    inventory["*"] = {"<default_policy>": dict(DEFAULT_POLICY)}
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(inventory, f, ensure_ascii=False, indent=2)
    return inventory
