"""结构版本注册与向前兼容检查。

保留 register(version) / resolve(version) 两个入口，在此之上提供：

* 每次注册记录字段、类型、默认值，版本号单调递增；
* 注册前做向前兼容检查，破坏性变更需显式标记并登记审批人；
* 按版本解析、回滚，回滚后版本链保留、旧版本仍可读；
* 版本链、兼容结论、回滚点与审计记录查询。

仅使用 Python 标准库。
"""

import copy
import datetime
import logging

_audit_logger = logging.getLogger("schemaversion.audit")

_NO_DEFAULT = object()


class SchemaVersionError(ValueError):
    """schemaversion 所有异常的基类（同时是 ValueError）。"""


class IncompatibleSchemaError(SchemaVersionError):
    """新版本包含破坏性变更，但未显式标记并审批。"""


class UnknownVersionError(SchemaVersionError, KeyError):
    """请求的版本不在版本链中（同时可按 KeyError 捕获）。"""


# 测试会直接把 _current 重置为 {"version": 0, "fields": {}}，
# 因此全部状态都挂在 _current 上，内部键由 _ensure_state 惰性补齐。
_current = {"version": 0, "fields": {}}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ensure_state():
    state = _current
    state.setdefault("chain", [])
    state.setdefault("audit", [])
    state.setdefault("rollback_points", [])
    return state


def _check_version_number(number):
    if isinstance(number, bool) or not isinstance(number, int):
        raise ValueError("版本号必须是整数")
    return number


def _normalize_fields(fields):
    """把字段定义归一化为 {name: {"type": str, "default": 值或 _NO_DEFAULT}}。"""
    normalized = {}
    for name, spec in (fields or {}).items():
        if isinstance(spec, str):
            normalized[name] = {"type": spec, "default": _NO_DEFAULT}
        elif isinstance(spec, dict) and "type" in spec:
            default = copy.deepcopy(spec["default"]) if "default" in spec else _NO_DEFAULT
            normalized[name] = {"type": spec["type"], "default": default}
        else:
            raise ValueError(
                "字段 %r 的定义必须是类型字符串或含 type 的字典" % (name,)
            )
    return normalized


def _public_fields(fields):
    """对外字段视图：无默认值只给类型字符串，有默认值给 type/default 字典。"""
    view = {}
    for name, spec in fields.items():
        if spec["default"] is _NO_DEFAULT:
            view[name] = spec["type"]
        else:
            view[name] = {
                "type": spec["type"],
                "default": copy.deepcopy(spec["default"]),
            }
    return view


def _same_default(a, b):
    if a is _NO_DEFAULT or b is _NO_DEFAULT:
        return a is b
    return a == b


def _diff_fields(old_fields, new_fields):
    added, removed, type_changed, default_changed = [], [], [], []
    for name, spec in new_fields.items():
        if name not in old_fields:
            added.append(
                {
                    "field": name,
                    "type": spec["type"],
                    "has_default": spec["default"] is not _NO_DEFAULT,
                }
            )
            continue
        old = old_fields[name]
        if old["type"] != spec["type"]:
            type_changed.append(
                {"field": name, "from": old["type"], "to": spec["type"]}
            )
        elif not _same_default(old["default"], spec["default"]):
            default_changed.append({"field": name})
    for name, old in old_fields.items():
        if name not in new_fields:
            removed.append({"field": name, "type": old["type"]})
    return {
        "added": added,
        "removed": removed,
        "type_changed": type_changed,
        "default_changed": default_changed,
    }


def _change_descriptions(changes):
    descriptions = []
    for item in changes["added"]:
        if item["has_default"]:
            descriptions.append("新增带默认值字段 %s" % item["field"])
        else:
            descriptions.append("新增无默认值字段 %s" % item["field"])
    for item in changes["removed"]:
        descriptions.append("删除字段 %s" % item["field"])
    for item in changes["type_changed"]:
        descriptions.append(
            "字段 %s 类型 %s -> %s"
            % (item["field"], item["from"], item["to"])
        )
    for item in changes["default_changed"]:
        descriptions.append("字段 %s 默认值变更" % item["field"])
    return descriptions


def _is_breaking(changes):
    return bool(
        changes["removed"]
        or changes["type_changed"]
        or any(not item["has_default"] for item in changes["added"])
    )


def _record_view(record):
    return {
        "version": record["version"],
        "fields": _public_fields(record["fields"]),
        "compatible": record["compatible"],
        "breaking": record["breaking"],
        "approver": record["approver"],
        "changes": copy.deepcopy(record["changes"]),
        "registered_at": record["registered_at"],
    }


def _public_view(record):
    return {"version": record["version"], "fields": _public_fields(record["fields"])}


def register(version, breaking=False, approver=None):
    """注册一个新版本。

    版本号必须大于历史最大版本；注册前对照链上最新版本做向前兼容检查。
    含破坏性变更时必须 breaking=True 并提供非空字符串 approver。
    返回本次注册记录（含 version、兼容结论、变更明细等）。
    """
    state = _ensure_state()
    if not isinstance(version, dict) or "version" not in version:
        raise ValueError("register 需要形如 {'version': int, 'fields': {...}} 的字典")
    number = _check_version_number(version["version"])

    latest = max((item["version"] for item in state["chain"]), default=0)
    if number <= latest:
        raise ValueError("版本号必须单调递增，当前最大版本为 %s" % latest)

    new_fields = _normalize_fields(version.get("fields") or {})
    previous_fields = state["chain"][-1]["fields"] if state["chain"] else {}
    changes = _diff_fields(previous_fields, new_fields)
    # 首版在空基线上建立结构，不构成对既有版本的破坏性变更
    breaking_change = bool(state["chain"]) and _is_breaking(changes)

    if breaking_change:
        if not breaking:
            raise IncompatibleSchemaError(
                "版本 %s 含破坏性变更（%s），需 breaking=True 显式标记"
                % (number, "；".join(_change_descriptions(changes)))
            )
        if not isinstance(approver, str) or not approver.strip():
            raise ValueError("破坏性变更需要审批人：approver 必须是非空字符串")

    record = {
        "version": number,
        "fields": new_fields,
        "compatible": not breaking_change,
        "breaking": breaking_change,
        "approver": approver if breaking_change else None,
        "changes": changes,
        "registered_at": _now(),
    }
    state["chain"].append(record)
    state["version"] = number
    state["fields"] = new_fields

    event = {
        "event": "register",
        "at": record["registered_at"],
        "version": number,
        "compatible": record["compatible"],
        "breaking": record["breaking"],
        "approver": record["approver"],
        "changes": copy.deepcopy(changes),
    }
    state["audit"].append(event)
    _audit_logger.info(
        "register v%s compatible=%s breaking=%s approver=%s",
        number,
        record["compatible"],
        record["breaking"],
        record["approver"],
    )
    return _record_view(record)


def resolve(version=None):
    """按版本解析结构；不传版本号返回当前版本。

    回滚后版本链仍然保留，任意已注册版本都可解析；版本 0 是空基线。
    未知版本抛 UnknownVersionError（同时是 KeyError）。
    """
    state = _ensure_state()
    if version is None:
        return {"version": state["version"], "fields": _public_fields(state["fields"])}
    number = _check_version_number(version)
    if number == 0:
        return {"version": 0, "fields": {}}
    for record in state["chain"]:
        if record["version"] == number:
            return _public_view(record)
    raise UnknownVersionError("未知版本: %s" % number)


def rollback(version):
    """把当前版本指针回滚到已注册版本，版本链与审计记录保留。

    回滚后旧版本仍可通过 resolve(version) 读取；之后新注册的版本号
    仍需大于历史最大版本。未知目标抛 UnknownVersionError。
    """
    state = _ensure_state()
    number = _check_version_number(version)
    if number == 0:
        target = {"version": 0, "fields": {}}
    else:
        target = next(
            (record for record in state["chain"] if record["version"] == number),
            None,
        )
    if target is None:
        raise UnknownVersionError("回滚目标版本不存在: %s" % number)
    if state["version"] == number:
        return {"version": state["version"], "fields": _public_fields(state["fields"])}

    event = {
        "event": "rollback",
        "at": _now(),
        "from": state["version"],
        "to": number,
    }
    state["audit"].append(event)
    state["rollback_points"].append(
        {"version": number, "from": event["from"], "at": event["at"]}
    )
    state["version"] = number
    # 记录中的字段结构只在注册时整体重建、不会就地修改，直接引用即可；
    # deepcopy 会让 _NO_DEFAULT 哨兵失去同一性，导致默认值判断失效。
    state["fields"] = target["fields"]
    _audit_logger.info("rollback v%s -> v%s", event["from"], number)
    return {"version": number, "fields": _public_fields(state["fields"])}


def chain():
    """返回完整版本链（注册顺序，含每版兼容结论与变更明细）。"""
    state = _ensure_state()
    return [_record_view(record) for record in state["chain"]]


def rollback_points():
    """返回全部回滚点（含来源版本与时间）。"""
    state = _ensure_state()
    return copy.deepcopy(state["rollback_points"])


def audit():
    """返回审计记录（注册、回滚事件），同时写入 schemaversion.audit 日志通道。"""
    state = _ensure_state()
    return copy.deepcopy(state["audit"])


def report():
    """输出版本链、兼容结论和回滚点的文本摘要。"""
    state = _ensure_state()
    lines = ["版本链:"]
    if not state["chain"]:
        lines.append("  （空，当前为基线版本 0）")
    for record in state["chain"]:
        names = ", ".join(record["fields"].keys()) or "（无字段）"
        lines.append("  v%s 字段=%s" % (record["version"], names))

    lines.append("兼容结论:")
    for index, record in enumerate(state["chain"]):
        if index == 0:
            lines.append("  v%s 建立基线（首次注册，无兼容性约束）" % record["version"])
            continue
        descriptions = _change_descriptions(record["changes"])
        detail = "（%s）" % "；".join(descriptions) if descriptions else ""
        if record["breaking"]:
            lines.append(
                "  v%s 破坏性变更%s，审批人=%s"
                % (record["version"], detail, record["approver"])
            )
        else:
            lines.append("  v%s 向前兼容%s" % (record["version"], detail))

    lines.append("回滚点:")
    if not state["rollback_points"]:
        lines.append("  （无）")
    for point in state["rollback_points"]:
        lines.append(
            "  v%s（自 v%s 回滚，%s）"
            % (point["version"], point["from"], point["at"])
        )
    return "\n".join(lines)
