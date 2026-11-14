"""数据契约双向兼容校验。

契约格式（均为可选键）::

    {
        "version": 1,                      # 契约版本号，归档用
        "fields": ["id", "amount"],        # 字段列表
        "types": {"id": "int"},            # 字段类型
        "required": ["id"],                # 必填字段（缺省全部可选）
        "defaults": {"amount": 0},         # 字段默认值
        "enums": {"status": ["a", "b"]},   # 字段枚举取值
    }

方向定义:
    backward —— 新契约能读旧契约写出的数据
    forward  —— 旧契约能读新契约写出的数据
"""

import json

# 已知类型放宽格：(old, new) 为放宽；反向即收紧。
_TYPE_WIDENING = {("int", "float")}


def _issue(direction, field, kind, severity, message, suggestion):
    return {
        "direction": direction,
        "field": field,
        "kind": kind,
        "severity": severity,
        "message": message,
        "suggestion": suggestion,
    }


def _required(contract):
    req = contract.get("required")
    if req is None:
        return set()
    if isinstance(req, dict):
        return {name for name, flag in req.items() if flag}
    return set(req)


def check(old, new):
    """比较 old 与 new 两版契约，返回双向兼容结论。

    返回字典包含:
        compatible            双向均无破坏项
        backward_compatible   新契约可读旧数据
        forward_compatible    旧契约可读新数据
        issues                全部问题项（含方向、字段、分级、修复建议）
        breaking              其中的破坏项
        suggestions           去重后的修复建议
    """
    issues = []
    old_fields = list(old.get("fields") or [])
    new_fields = list(new.get("fields") or [])
    old_required = _required(old)
    new_required = _required(new)
    old_types = old.get("types") or {}
    new_types = new.get("types") or {}
    old_defaults = old.get("defaults") or {}
    new_defaults = new.get("defaults") or {}
    old_enums = old.get("enums") or {}
    new_enums = new.get("enums") or {}

    for name in old_fields:
        if name not in new_fields:
            issues.append(_issue(
                "forward", name, "field_removed", "breaking",
                "字段 %s 被删除，旧读取方在新数据中找不到该列" % name,
                "恢复字段 %s，或先迁移下游读取方再删除" % name,
            ))
    for name in new_fields:
        if name not in old_fields and name in new_required:
            issues.append(_issue(
                "backward", name, "required_field_added", "warning",
                "新增必填字段 %s，旧数据没有该列" % name,
                "将 %s 改为可选，或为其提供默认值" % name,
            ))

    common = [name for name in old_fields if name in new_fields]
    for name in common:
        old_type = old_types.get(name)
        new_type = new_types.get(name)
        if old_type and new_type and old_type != new_type:
            if (old_type, new_type) in _TYPE_WIDENING:
                issues.append(_issue(
                    "forward", name, "type_widened", "warning",
                    "字段 %s 类型由 %s 放宽为 %s" % (name, old_type, new_type),
                    "确认旧读取方能处理 %s 值，或为 %s 做类型转换" % (new_type, name),
                ))
            elif (new_type, old_type) in _TYPE_WIDENING:
                issues.append(_issue(
                    "backward", name, "type_tightened", "breaking",
                    "字段 %s 类型由 %s 收紧为 %s，旧数据可能含非法值" % (name, old_type, new_type),
                    "保持类型 %s，或先清洗旧数据再收紧" % old_type,
                ))
            else:
                issues.append(_issue(
                    "both", name, "type_changed", "warning",
                    "字段 %s 类型由 %s 变为 %s，不在已知放宽规则内" % (name, old_type, new_type),
                    "确认读写双方都能处理类型 %s" % new_type,
                ))

        if name in old_defaults and name in new_defaults:
            if old_defaults[name] != new_defaults[name]:
                issues.append(_issue(
                    "backward", name, "default_changed", "warning",
                    "字段 %s 默认值由 %r 变为 %r" % (name, old_defaults[name], new_defaults[name]),
                    "默认值变化会改变旧数据的读取结果，确认下游可接受或保持原默认值",
                ))
        elif name in old_defaults:
            issues.append(_issue(
                "backward", name, "default_changed", "warning",
                "字段 %s 默认值 %r 被移除" % (name, old_defaults[name]),
                "保留默认值，或确认旧数据缺失该列时无需回填",
            ))
        elif name in new_defaults:
            issues.append(_issue(
                "backward", name, "default_changed", "warning",
                "字段 %s 新增默认值 %r" % (name, new_defaults[name]),
                "新增默认值会改变旧数据的读取结果，确认下游可接受",
            ))

        old_enum = old_enums.get(name)
        new_enum = new_enums.get(name)
        if old_enum and new_enum:
            removed_values = [v for v in old_enum if v not in new_enum]
            if removed_values:
                issues.append(_issue(
                    "backward", name, "enum_shrunk", "warning",
                    "字段 %s 枚举收缩，移除取值 %r" % (name, removed_values),
                    "保留枚举取值，或先迁移含这些取值的旧数据",
                ))

        if name in new_required and name not in old_required:
            issues.append(_issue(
                "backward", name, "required_changed", "warning",
                "字段 %s 由可选变为必填" % name,
                "保持可选，或为旧数据补齐该字段",
            ))
        elif name in old_required and name not in new_required:
            issues.append(_issue(
                "forward", name, "required_changed", "warning",
                "字段 %s 由必填变为可选，新数据可能缺失该列" % name,
                "确认旧读取方能容忍该列缺失",
            ))

    issues.sort(key=lambda i: (i["field"], i["kind"], i["direction"]))
    breaking = [i for i in issues if i["severity"] == "breaking"]

    def blocked(direction):
        return any(i["direction"] in (direction, "both") for i in breaking)

    suggestions = []
    for item in issues:
        if item["suggestion"] not in suggestions:
            suggestions.append(item["suggestion"])

    return {
        "compatible": not breaking,
        "backward_compatible": not blocked("backward"),
        "forward_compatible": not blocked("forward"),
        "issues": issues,
        "breaking": breaking,
        "suggestions": suggestions,
    }


def save(contract, path):
    """把契约归档为 JSON 文件，键排序保证重复归档内容一致。"""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(contract, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def load(path):
    """回读 save 归档的契约文件。"""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

