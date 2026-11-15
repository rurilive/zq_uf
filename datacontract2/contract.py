"""数据契约双向兼容检查。

方向定义：
- 读方向（read）：新契约的读取方去读按旧契约写入的数据。
- 写方向（write）：新契约的写入方写给仍按旧契约读取的下游。

顶层 ``compatible`` / ``breaking`` / ``direction`` 保持读方向语义（兼容旧行为），
完整的双向结论在 ``read`` / ``write`` 子结果和 ``changes`` 明细里。
"""

import json
import os

# 类型宽严次序：rank 越小越窄。宽类型可以容纳窄类型的值。
_TYPE_RANK = {"bool": 0, "int": 1, "float": 2, "text": 3}

BREAKING = "breaking"
WARNING = "warning"
INFO = "info"

_LEVEL_TEXT = {BREAKING: "破坏", WARNING: "警告", INFO: "建议"}


def _spec_of(field_spec):
    """字段声明可以是类型字符串（"int"）或完整字典，统一成字典。"""
    if isinstance(field_spec, str):
        return {"type": field_spec}
    return dict(field_spec)


def _flatten(fields, prefix=""):
    """展开嵌套字段，返回 {点分路径: 规范字典}。"""
    flat = {}
    for name, spec in fields.items():
        spec = _spec_of(spec)
        path = prefix + name
        flat[path] = spec
        nested = spec.get("fields")
        if isinstance(nested, dict):
            flat.update(_flatten(nested, path + "."))
    return flat


def _add_change(changes, direction, level, kind, path, message, suggestion):
    changes.append({
        "direction": direction,
        "level": level,
        "kind": kind,
        "field": path.rsplit(".", 1)[-1],
        "path": path,
        "message": message,
        "suggestion": suggestion,
    })


def _check_version(old, new, changes):
    """契约版本号必须单调递增。任一版本缺版本号则跳过。"""
    old_v, new_v = old.get("version"), new.get("version")
    if old_v is None or new_v is None:
        return True
    if new_v > old_v:
        return True
    for direction in ("read", "write"):
        _add_change(
            changes, direction, WARNING, "version_not_monotonic", "version",
            "契约版本号 %r 未大于旧版本号 %r，版本号必须单调递增" % (new_v, old_v),
            "递增版本号后再发布新契约",
        )
    return False


def _field_removed(path, changes):
    _add_change(
        changes, "read", BREAKING, "field_removed", path,
        "字段 %s 被删除，读取方代码仍可能依赖它" % path,
        "保留该字段一个过渡期，或先下线所有读取方再删除",
    )
    _add_change(
        changes, "write", BREAKING, "field_removed", path,
        "字段 %s 被删除，旧读取方将取不到该字段" % path,
        "先停止下游对该字段的依赖，再删除",
    )


def _field_added(path, spec, changes):
    required = bool(spec.get("required"))
    has_default = "default" in spec
    if required and not has_default:
        _add_change(
            changes, "read", BREAKING, "field_added", path,
            "新增必填字段 %s 且无默认值，旧数据中没有该字段" % path,
            "改为可选字段，或提供默认值并回填历史数据",
        )
    else:
        _add_change(
            changes, "read", INFO, "field_added", path,
            "新增字段 %s 对读取方兼容" % path,
            "新字段保持可选或带默认值即可安全上线",
        )
    _add_change(
        changes, "write", INFO, "field_added", path,
        "新增字段 %s，旧读取方会忽略它" % path,
        "确认下游按未知字段容错处理",
    )


def _compare_type(path, old_spec, new_spec, changes):
    old_t, new_t = old_spec.get("type"), new_spec.get("type")
    if old_t == new_t:
        return
    old_r, new_r = _TYPE_RANK.get(old_t), _TYPE_RANK.get(new_t)
    if old_r is None or new_r is None:
        for direction in ("read", "write"):
            _add_change(
                changes, direction, WARNING, "type_changed_unknown", path,
                "字段 %s 类型从 %r 变为 %r，无法判定宽严" % (path, old_t, new_t),
                "人工确认两个类型的赋值兼容性",
            )
        return
    if new_r > old_r:  # 类型放宽
        _add_change(
            changes, "read", INFO, "type_widened", path,
            "字段 %s 类型放宽 %s -> %s，旧数据仍可读取" % (path, old_t, new_t),
            "无需迁移",
        )
        _add_change(
            changes, "write", BREAKING, "type_widened", path,
            "字段 %s 类型放宽 %s -> %s，旧读取方按窄类型解析会失败" % (path, old_t, new_t),
            "先升级下游读取方，或保持窄类型",
        )
    else:  # 类型收紧
        _add_change(
            changes, "read", BREAKING, "type_narrowed", path,
            "字段 %s 类型收紧 %s -> %s，旧数据可能不满足新类型" % (path, old_t, new_t),
            "先清洗不满足新类型的历史数据，再切换契约",
        )
        _add_change(
            changes, "write", INFO, "type_narrowed", path,
            "字段 %s 类型收紧 %s -> %s，旧读取方仍可解析" % (path, old_t, new_t),
            "无需迁移",
        )


def _compare_default(path, old_spec, new_spec, changes):
    old_has, new_has = "default" in old_spec, "default" in new_spec
    if not old_has and new_has:
        for direction in ("read", "write"):
            _add_change(
                changes, direction, INFO, "default_added", path,
                "字段 %s 新增默认值 %r" % (path, new_spec["default"]),
                "读取历史数据时按默认值补齐，注意与显式写入的语义差异",
            )
    elif old_has and not new_has:
        for direction in ("read", "write"):
            _add_change(
                changes, direction, WARNING, "default_removed", path,
                "字段 %s 默认值 %r 被移除" % (path, old_spec["default"]),
                "确认写入方不再依赖隐式默认值",
            )
    elif old_has and new_has and old_spec["default"] != new_spec["default"]:
        for direction in ("read", "write"):
            _add_change(
                changes, direction, WARNING, "default_changed", path,
                "字段 %s 默认值从 %r 修改为 %r" % (path, old_spec["default"], new_spec["default"]),
                "默认值只影响新数据，确认新旧数据语义一致，必要时回填",
            )


def _compare_enum(path, old_spec, new_spec, changes):
    old_e, new_e = old_spec.get("enum"), new_spec.get("enum")
    if old_e is None and new_e is None:
        return
    if old_e is None:  # 新增枚举约束 = 值域收紧
        _add_change(
            changes, "read", BREAKING, "enum_narrowed", path,
            "字段 %s 新增枚举约束，旧数据可能含约束外的值" % path,
            "先清洗不在枚举内的历史数据",
        )
        _add_change(
            changes, "write", INFO, "enum_narrowed", path,
            "字段 %s 新增枚举约束，对旧读取方兼容" % path,
            "无需迁移",
        )
        return
    if new_e is None:  # 移除枚举约束 = 值域放宽
        _add_change(
            changes, "read", INFO, "enum_widened", path,
            "字段 %s 移除枚举约束，对读取方兼容" % path,
            "无需迁移",
        )
        _add_change(
            changes, "write", BREAKING, "enum_widened", path,
            "字段 %s 移除枚举约束，旧读取方可能收到枚举外的值" % path,
            "先升级下游再放开约束",
        )
        return
    removed = [v for v in old_e if v not in new_e]
    added = [v for v in new_e if v not in old_e]
    if removed:
        _add_change(
            changes, "read", BREAKING, "enum_removed", path,
            "字段 %s 删除枚举值 %r，旧数据可能仍含这些值" % (path, removed),
            "先迁移含被删枚举值的历史数据",
        )
        _add_change(
            changes, "write", INFO, "enum_removed", path,
            "字段 %s 删除枚举值 %r，新数据不会再出现，对旧读取方兼容" % (path, removed),
            "无需迁移",
        )
    if added:
        _add_change(
            changes, "read", INFO, "enum_added", path,
            "字段 %s 新增枚举值 %r，对读取方兼容" % (path, added),
            "无需迁移",
        )
        _add_change(
            changes, "write", BREAKING, "enum_added", path,
            "字段 %s 新增枚举值 %r，旧读取方不认识新值" % (path, added),
            "先升级下游读取方再启用新枚举值",
        )


def _compare_required(path, old_spec, new_spec, changes):
    old_r = bool(old_spec.get("required"))
    new_r = bool(new_spec.get("required"))
    if not old_r and new_r:
        _add_change(
            changes, "read", BREAKING, "required_added", path,
            "字段 %s 由可选变为必填，旧数据可能缺该字段" % path,
            "提供默认值或回填历史数据后再收紧",
        )
        _add_change(
            changes, "write", INFO, "required_added", path,
            "字段 %s 变为必填，写入方必须提供，对旧读取方无影响" % path,
            "确认所有写入路径都已赋值",
        )
    elif old_r and not new_r:
        _add_change(
            changes, "read", INFO, "required_removed", path,
            "字段 %s 由必填变为可选，对读取方兼容" % path,
            "无需迁移",
        )
        _add_change(
            changes, "write", BREAKING, "required_removed", path,
            "字段 %s 由必填变为可选，新数据可能缺该字段，旧读取方仍指望它" % path,
            "先让下游容忍缺失，再放开必填",
        )


def _field_changed(path, old_spec, new_spec, changes):
    _compare_type(path, old_spec, new_spec, changes)
    _compare_default(path, old_spec, new_spec, changes)
    _compare_enum(path, old_spec, new_spec, changes)
    _compare_required(path, old_spec, new_spec, changes)


def _direction_result(changes, direction):
    mine = [c for c in changes if c["direction"] == direction]
    breaking = sorted({c["path"] for c in mine if c["level"] == BREAKING})
    return {
        "compatible": not breaking,
        "breaking": breaking,
        "changes": mine,
    }


def _migration_script(old, new, changes):
    """为破坏性变更生成迁移脚本建议（SQL 风格）。"""
    table = new.get("name") or old.get("name") or "<table>"
    new_fields = _flatten(new.get("fields", {}))
    lines = []
    seen = set()
    for c in changes:
        if c["level"] != BREAKING:
            continue
        key = (c["kind"], c["path"])
        if key in seen:
            continue
        seen.add(key)
        kind, path, field = c["kind"], c["path"], c["field"]
        if kind == "field_removed":
            lines.append("-- 字段 %s 被删除：先确认下游不再读取" % path)
            lines.append("ALTER TABLE %s DROP COLUMN %s;" % (table, field))
        elif kind == "type_narrowed":
            new_t = new_fields.get(path, {}).get("type", "<type>")
            lines.append("-- 字段 %s 类型收紧：先清洗不满足 %s 的历史数据" % (path, new_t))
            lines.append("ALTER TABLE %s ALTER COLUMN %s TYPE %s;" % (table, field, new_t))
        elif kind == "type_widened":
            lines.append("-- 字段 %s 类型放宽：先升级全部下游读取方，无需 DDL" % path)
        elif kind == "field_added":
            new_t = new_fields.get(path, {}).get("type", "<type>")
            lines.append("-- 新增必填字段 %s：加列时必须带默认值并回填" % path)
            lines.append(
                "ALTER TABLE %s ADD COLUMN %s %s NOT NULL DEFAULT <回填值>;" % (table, field, new_t)
            )
        elif kind in ("enum_removed", "enum_narrowed"):
            lines.append("-- 字段 %s 枚举收紧：先迁移取值越界的历史行" % path)
            lines.append("UPDATE %s SET %s = <新值> WHERE %s NOT IN (<新枚举>);" % (table, field, field))
        elif kind in ("enum_added", "enum_widened"):
            lines.append("-- 字段 %s 枚举放宽：先升级下游读取方，无需 DDL" % path)
        elif kind == "required_added":
            lines.append("-- 字段 %s 变为必填：先回填空值再加强约束" % path)
            lines.append("UPDATE %s SET %s = <回填值> WHERE %s IS NULL;" % (table, field, field))
        elif kind == "required_removed":
            lines.append("-- 字段 %s 变为可选：让下游容忍缺失，无需 DDL" % path)
    return lines


def check(old, new):
    """双向兼容检查。接口保持 check(old, new)。

    返回字典：
    - compatible / breaking / direction：读方向结论（兼容旧行为）。
    - read / write：各方向完整结论（compatible、breaking、changes）。
    - changes：全部变更明细，每条带 direction、level、kind、field、path、
      message、suggestion。
    - version_ok：版本号是否单调递增。
    - migration_script：破坏性变更的迁移脚本建议。
    """
    old_fields = _flatten(old.get("fields", {}))
    new_fields = _flatten(new.get("fields", {}))
    changes = []
    version_ok = _check_version(old, new, changes)
    for path in sorted(set(old_fields) | set(new_fields)):
        old_spec = old_fields.get(path)
        new_spec = new_fields.get(path)
        if old_spec is None:
            _field_added(path, new_spec, changes)
        elif new_spec is None:
            _field_removed(path, changes)
        else:
            _field_changed(path, old_spec, new_spec, changes)

    read = _direction_result(changes, "read")
    write = _direction_result(changes, "write")
    suggestions = []
    for c in changes:
        if c["suggestion"] and c["suggestion"] not in suggestions:
            suggestions.append(c["suggestion"])
    return {
        "compatible": read["compatible"],
        "breaking": read["breaking"],
        "warnings": [c["message"] for c in read["changes"] if c["level"] != BREAKING],
        "direction": "read",
        "suggestions": suggestions,
        "read": read,
        "write": write,
        "changes": changes,
        "version_ok": version_ok,
        "migration_script": _migration_script(old, new, changes),
    }


def matrix(old, new):
    """按字段输出读 / 写方向兼容矩阵（文本表格）。"""
    result = check(old, new)
    old_fields = _flatten(old.get("fields", {}))
    new_fields = _flatten(new.get("fields", {}))
    paths = sorted(set(old_fields) | set(new_fields))

    def status(direction, path):
        levels = [c["level"] for c in result[direction]["changes"] if c["path"] == path]
        if not levels:
            return "兼容"
        for level in (BREAKING, WARNING, INFO):
            if level in levels:
                return _LEVEL_TEXT[level]
        return "兼容"

    def note(path):
        msgs = [c["message"] for c in result["changes"] if c["path"] == path]
        return msgs[0] if msgs else ""

    header = ("字段", "读方向", "写方向", "说明")
    rows = [(p, status("read", p), status("write", p), note(p)) for p in paths]
    widths = [
        max(len(str(row[i])) for row in [header] + rows)
        for i in range(len(header))
    ]
    lines = ["  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(header))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def export(result, path=None):
    """把检查结果导出为 JSON 字符串；给了 path 就同时写文件。"""
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return text


class ContractHistory:
    """按版本保留历史契约，可对比任意两版。

    directory 给了就用 JSON 文件持久化（每版一个文件），否则只存内存。
    版本号必须单调递增，否则 add 抛 ValueError。
    """

    def __init__(self, directory=None):
        self._directory = directory
        self._contracts = []
        if directory and os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                if name.endswith(".json"):
                    with open(os.path.join(directory, name), encoding="utf-8") as f:
                        self._contracts.append(json.load(f))
            self._contracts.sort(key=lambda c: c.get("version", 0))

    def add(self, contract):
        if self._contracts:
            last_v = self._contracts[-1].get("version", 0)
            if contract.get("version", 0) <= last_v:
                raise ValueError(
                    "契约版本号必须单调递增：%r 不大于当前最新 %r" % (contract.get("version"), last_v)
                )
        self._contracts.append(contract)
        if self._directory:
            os.makedirs(self._directory, exist_ok=True)
            name = "contract_v%s.json" % contract.get("version", len(self._contracts))
            with open(os.path.join(self._directory, name), "w", encoding="utf-8") as f:
                json.dump(contract, f, ensure_ascii=False, indent=2)

    def latest(self):
        return self._contracts[-1] if self._contracts else None

    def compare(self, old_index=-2, new_index=-1):
        """对比历史中的两版，默认对比最近两版。"""
        return check(self._contracts[old_index], self._contracts[new_index])

    def __len__(self):
        return len(self._contracts)
