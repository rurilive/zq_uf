"""兼容性规则：每条规则独立声明 id / 默认是否启用 / 方向与级别。

判定约定（旧契约 -> 新契约，old 为线上在读/在写的版本）：
- 读取方向：新契约按旧类型解析数据。类型收紧（宽度变小）解析报错 -> 破坏；
  枚举删值后旧数据落不在新枚举里 -> 破坏。
- 写入方向：写入方按新契约产出。类型放宽后按旧类型写入的列存不下 -> 破坏；
  枚举加值后旧读取方不认识 -> 破坏。
- 删列两个方向都算破坏。
- 默认值、枚举、必填单独标记，不混进类型破坏里。
"""

from dataclasses import dataclass
from enum import Enum

from .schema import MISSING, NUMERIC_RANK, type_family


class Direction(str, Enum):
    READ = "read"
    WRITE = "write"
    BOTH = "both"


class Severity(str, Enum):
    COMPATIBLE = "compatible"
    INFO = "info"
    WARNING = "warning"
    BREAKING = "breaking"


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    enabled_by_default: bool = True
    # rule(path, old_field, new_field, ctx) -> dict | None
    # ctx 为 RuleBook.check 传入的共享上下文
    func: object = None


def _finding(rule, direction, severity, path, message, suggestion,
             old_value=None, new_value=None, **extra):
    data = {
        "rule": rule.id,
        "direction": direction.value,
        "severity": severity.value,
        "path": path,
        "message": message,
        "suggestion": suggestion,
        "old": old_value,
        "new": new_value,
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------- 字段增删

def _field_added(rule, path, old, new, ctx):
    if new is None or old is not None:
        return None
    findings = []
    if new["required"] and new["default"] is MISSING:
        # 读取方可忽略新增列；写入方必须产出且无默认值 -> 写入破坏
        findings.append(_finding(
            rule, Direction.WRITE, Severity.BREAKING, path,
            "新增必填字段且没有默认值，旧写入方不会产出该列",
            "给字段设置默认值，或先以非必填发布、等写入方全部升级后再改为必填"))
    else:
        findings.append(_finding(
            rule, Direction.WRITE, Severity.WARNING, path,
            "新增字段，旧写入方不会产出该列",
            "确认所有写入方升级计划，并为旧数据提供默认值或回填"))
    findings.append(_finding(
        rule, Direction.READ, Severity.COMPATIBLE, path,
        "新增字段，读取方忽略未知列即可",
        "无需处理"))
    return findings


def _field_removed(rule, path, old, new, ctx):
    if old is None or new is not None:
        return None
    # old 存在而 new 为 None：字段被删除，两个方向都破坏
    return _finding(
        rule, Direction.BOTH, Severity.BREAKING, path,
        "字段被删除：读取方按旧契约读取会缺列，写入方写出的列无人接收",
        "保留该字段并标记废弃；确需删除时先让全部读写方下线对它的依赖，再走下线流程")


# ---------------------------------------------------------------- 类型

def _type_changed(rule, path, old, new, ctx):
    if old is None or new is None or old["type"] == new["type"]:
        return None
    old_type, new_type = old["type"], new["type"]
    old_family, new_family = type_family(old_type), type_family(new_type)

    if old_family == "numeric" and new_family == "numeric":
        old_rank, new_rank = NUMERIC_RANK[old_type], NUMERIC_RANK[new_type]
        if new_rank < old_rank:
            return _finding(
                rule, Direction.READ, Severity.BREAKING, path,
                f"数值类型收紧：{old_type} -> {new_type}，旧数据可能超出新类型范围，读取解析报错",
                f"保持 {old_type} 或更宽的类型；如确需收紧，先确认存量数据都在 {new_type} 范围内并做迁移",
                old_type, new_type)
        return _finding(
            rule, Direction.WRITE, Severity.BREAKING, path,
            f"数值类型放宽：{old_type} -> {new_type}，按旧类型写入的列存不下放宽后的值",
            f"保持 {old_type}；如确需放宽，存储层与全部按 {old_type} 解析的写入方需先升级",
            old_type, new_type)

    # 跨家族（int<->text 等）：两个方向都破坏
    return _finding(
        rule, Direction.BOTH, Severity.BREAKING, path,
        f"类型家族变更：{old_type} -> {new_type}，读写双方的编解码都不兼容",
        "用新字段名承载新类型，旧字段保留废弃；不要原地改类型",
        old_type, new_type)


# ---------------------------------------------------------------- 默认值

def _default_changed(rule, path, old, new, ctx):
    if old is None or new is None:
        return None
    if old["default"] is MISSING and new["default"] is MISSING:
        return None
    if old["default"] == new["default"]:
        return None
    from .schema import render_value
    if old["default"] is MISSING:
        message = "从无默认值改为有默认值"
    elif new["default"] is MISSING:
        message = "从有默认值改为无默认值"
    else:
        message = "默认值发生变更"
    return _finding(
        rule, Direction.BOTH, Severity.INFO, path,
        f"{message}：{render_value(old['default'])} -> {render_value(new['default'])}",
        "确认存量数据的回填策略与下游对默认值的假设",
        render_value(old["default"]), render_value(new["default"]))


# ---------------------------------------------------------------- 枚举

def _enum_changed(rule, path, old, new, ctx):
    if old is None or new is None:
        return None
    if old["enum"] is None and new["enum"] is None:
        return None
    if old["enum"] == new["enum"]:
        return None

    old_set = set(old["enum"] or ())
    new_set = set(new["enum"] or ())
    added, removed = sorted(new_set - old_set), sorted(old_set - new_set)
    findings = []

    if old["enum"] is None or new["enum"] is None:
        # 枚举开关变更，单独标记，不判破坏
        findings.append(_finding(
            rule, Direction.BOTH, Severity.INFO, path,
            f"枚举约束开关变更：{old['enum']} -> {new['enum']}",
            "确认上下游是否依赖枚举约束做校验",
            old["enum"], new["enum"]))
        return findings

    if removed:
        findings.append(_finding(
            rule, Direction.READ, Severity.BREAKING, path,
            f"枚举删除取值 {removed}，存量数据可能仍带该值，读取方校验失败",
            f"先停止写入 {removed} 并清洗存量数据，再从枚举中移除",
            old["enum"], new["enum"], enum_added=added, enum_removed=removed))
    if added:
        findings.append(_finding(
            rule, Direction.WRITE, Severity.WARNING, path,
            f"枚举新增取值 {added}，旧读取方可能不认识新值",
            "先升级全部读取方使其接受新取值，再放开写入",
            old["enum"], new["enum"], enum_added=added, enum_removed=removed))
    return findings


# ---------------------------------------------------------------- 必填

def _required_changed(rule, path, old, new, ctx):
    if old is None or new is None or old["required"] == new["required"]:
        return None
    if new["required"] and new["default"] is MISSING:
        return _finding(
            rule, Direction.WRITE, Severity.BREAKING, path,
            "字段由可选变为必填且没有默认值，旧写入方不产出该列时校验失败",
            "先加默认值或以非必填发布，待写入方全部升级后再改为必填",
            old["required"], new["required"])
    return _finding(
        rule, Direction.READ, Severity.INFO, path,
        f"必填约束变更：required {old['required']} -> {new['required']}",
        "确认读取方对空值的处理",
        old["required"], new["required"])


# 规则 id 即 finding 的类别，gate 按规则 id 做影子比对
RULES = [
    Rule("field.added", "新增字段", True, _field_added),
    Rule("field.removed", "删除字段", True, _field_removed),
    Rule("type.changed", "类型变化（收紧/放宽/跨家族）", True, _type_changed),
    Rule("default.changed", "默认值变化（单独标记）", True, _default_changed),
    Rule("enum.changed", "枚举变化（单独标记）", True, _enum_changed),
    Rule("required.changed", "必填约束变化（单独标记）", True, _required_changed),
]
