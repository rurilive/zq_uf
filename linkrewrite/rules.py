"""重写规则模型与合法性校验。

一条规则按站点注册，声明三类参数（tracking / pagination / other）各自的动作：

* ``retain``：白名单，命中规则后仅保留列表中的参数（未声明 retain 则不过滤）
* ``delete``：删除指定参数
* ``inject``：注入参数，可为静态值或带校验器的描述（见 :class:`Rule`）

规则按 ``priority`` 从大到小匹配；同优先级按注册先后排序。
"""

import re


class InvalidRule(ValueError):
    """规则声明或注入值不合法。"""


# 参数名：字母数字开头，允许点、横线、下划线、方括号
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\[\]-]*$")

_SCALAR_TYPES = (str, int, float, bool)


def _check_name(name):
    if not isinstance(name, str) or not name:
        raise InvalidRule("参数名必须是非空字符串: %r" % (name,))
    if not _NAME_RE.match(name):
        raise InvalidRule("参数名含非法字符: %r" % (name,))


def _check_string_list(value, field, rule_id):
    if not isinstance(value, (list, tuple)):
        raise InvalidRule("规则 %r 的 %s 必须是列表" % (rule_id, field))
    seen = set()
    for name in value:
        _check_name(name)
        if name in seen:
            raise InvalidRule(
                "规则 %r 的 %s 中参数 %r 重复" % (rule_id, field, name)
            )
        seen.add(name)


def _check_injector(name, spec, rule_id):
    """校验单个注入项，返回规范化后的 (值描述字典)。"""
    if isinstance(spec, _SCALAR_TYPES):
        return {"value": str(spec)}
    if spec is None or not isinstance(spec, dict):
        raise InvalidRule(
            "规则 %r 注入参数 %r 的值必须是标量或字典" % (rule_id, name)
        )
    if "value" not in spec:
        raise InvalidRule(
            "规则 %r 注入参数 %r 缺少 value" % (rule_id, name)
        )
    value = spec["value"]
    if not isinstance(value, _SCALAR_TYPES):
        raise InvalidRule(
            "规则 %r 注入参数 %r 的 value 必须是字符串/数字/布尔值"
            % (rule_id, name)
        )

    normalized = {"value": str(value)}
    allowed = spec.get("allowed")
    if allowed is not None:
        if not isinstance(allowed, (list, tuple)) or not allowed:
            raise InvalidRule(
                "规则 %r 注入参数 %r 的 allowed 必须是非空列表"
                % (rule_id, name)
            )
        allowed = [str(item) for item in allowed]
        if normalized["value"] not in allowed:
            raise InvalidRule(
                "规则 %r 注入参数 %r 的值 %r 不在允许集合 %s 内"
                % (rule_id, name, normalized["value"], allowed)
            )
        normalized["allowed"] = allowed

    pattern = spec.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise InvalidRule(
                "规则 %r 注入参数 %r 的 pattern 必须是字符串"
                % (rule_id, name)
            )
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise InvalidRule(
                "规则 %r 注入参数 %r 的 pattern 不是合法正则: %s"
                % (rule_id, name, exc)
            )
        if not compiled.search(normalized["value"]):
            raise InvalidRule(
                "规则 %r 注入参数 %r 的值 %r 不匹配 pattern %r"
                % (rule_id, name, normalized["value"], pattern)
            )
        normalized["pattern"] = pattern

    for bound in ("min", "max"):
        if bound in spec and spec[bound] is not None:
            bound_value = spec[bound]
            if isinstance(bound_value, bool) or not isinstance(
                bound_value, int
            ):
                raise InvalidRule(
                    "规则 %r 注入参数 %r 的 %s 必须是整数"
                    % (rule_id, bound, name)
                )
            normalized[bound] = bound_value
    if "min" in normalized and "max" in normalized and normalized["min"] > normalized["max"]:
        raise InvalidRule(
            "规则 %r 注入参数 %r 的 min 大于 max" % (rule_id, name)
        )

    int_value = None
    if "min" in normalized or "max" in normalized:
        try:
            int_value = int(normalized["value"])
        except (TypeError, ValueError):
            raise InvalidRule(
                "规则 %r 注入参数 %r 声明了 min/max，值 %r 必须是整数"
                % (rule_id, name, normalized["value"])
            )
        if "min" in normalized and int_value < normalized["min"]:
            raise InvalidRule(
                "规则 %r 注入参数 %r 的值 %s 小于 min=%s"
                % (rule_id, name, int_value, normalized["min"])
            )
        if "max" in normalized and int_value > normalized["max"]:
            raise InvalidRule(
                "规则 %r 注入参数 %r 的值 %s 大于 max=%s"
                % (rule_id, name, int_value, normalized["max"])
            )
    return normalized


class Rule:
    """一条站点级重写规则。

    :param id: 规则唯一标识
    :param priority: 优先级，数字越大越优先
    :param path: 可选，路径匹配正则（``re.search``）；缺省匹配所有路径
    :param description: 可选说明
    :param tracking/pagination/other: 各参数类别的动作字典，键为
        ``retain`` / ``delete`` / ``inject``
    """

    def __init__(
        self,
        id,
        priority=0,
        path=None,
        description=None,
        tracking=None,
        pagination=None,
        other=None,
    ):
        if not isinstance(id, str) or not id.strip():
            raise InvalidRule("规则 id 必须是非空字符串")
        self.id = id
        self.priority = priority
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise InvalidRule("规则 %r 的 priority 必须是整数" % id)
        self.path = path
        self.path_re = None
        if path is not None:
            if not isinstance(path, str):
                raise InvalidRule("规则 %r 的 path 必须是字符串" % id)
            try:
                self.path_re = re.compile(path)
            except re.error as exc:
                raise InvalidRule(
                    "规则 %r 的 path 不是合法正则: %s" % (id, exc)
                )
        self.description = description
        self.actions = {}
        for cls, raw in (
            ("tracking", tracking),
            ("pagination", pagination),
            ("other", other),
        ):
            self.actions[cls] = self._validate_section(cls, raw)

    def _validate_section(self, cls, raw):
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise InvalidRule(
                "规则 %r 的 %s 段必须是字典" % (self.id, cls)
            )
        unknown = set(raw) - {"retain", "delete", "inject"}
        if unknown:
            raise InvalidRule(
                "规则 %r 的 %s 段含未知动作 %s"
                % (self.id, cls, sorted(unknown))
            )
        section = {}
        if "retain" in raw:
            _check_string_list(raw["retain"], "%s.retain" % cls, self.id)
            section["retain"] = list(raw["retain"])
        if "delete" in raw:
            _check_string_list(raw["delete"], "%s.delete" % cls, self.id)
            section["delete"] = list(raw["delete"])
        if "inject" in raw:
            inject = raw["inject"]
            if not isinstance(inject, dict) or not inject:
                raise InvalidRule(
                    "规则 %r 的 %s.inject 必须是非空字典" % (self.id, cls)
                )
            normalized_inject = {}
            for name, spec in inject.items():
                _check_name(name)
                normalized_inject[name] = _check_injector(
                    name, spec, self.id
                )
            section["inject"] = normalized_inject

        retain = set(section.get("retain", []))
        delete = set(section.get("delete", []))
        overlap = retain & delete
        if overlap:
            raise InvalidRule(
                "规则 %r 的 %s 段中参数 %s 同时出现在 retain 和 delete"
                % (self.id, cls, sorted(overlap))
            )
        inject_names = set(section.get("inject", {}))
        inject_overlap = inject_names & (retain | delete)
        if inject_overlap:
            raise InvalidRule(
                "规则 %r 的 %s 段中注入参数 %s 与 retain/delete 冲突"
                % (self.id, cls, sorted(inject_overlap))
            )
        return section

    def matches_path(self, path):
        if self.path_re is None:
            return True
        return bool(self.path_re.search(path or "/"))

    def to_dict(self):
        """输出与 :func:`rule_from_dict` 入参同构的扁平字典（可往返恢复）。"""
        return {
            "id": self.id,
            "priority": self.priority,
            "path": self.path,
            "description": self.description,
            "tracking": self._dump_section("tracking"),
            "pagination": self._dump_section("pagination"),
            "other": self._dump_section("other"),
        }

    def _dump_section(self, cls):
        section = self.actions.get(cls, {})
        if not section:
            return None
        dumped = {}
        if "retain" in section:
            dumped["retain"] = list(section["retain"])
        if "delete" in section:
            dumped["delete"] = list(section["delete"])
        if "inject" in section:
            dumped["inject"] = {
                name: dict(spec) for name, spec in section["inject"].items()
            }
        return dumped


def rule_from_dict(data):
    """从普通字典构造 :class:`Rule`，构造时即完成全部合法性校验。"""
    if not isinstance(data, dict):
        raise InvalidRule("规则声明必须是字典")
    if "id" not in data:
        raise InvalidRule("规则声明缺少 id")
    return Rule(
        id=data["id"],
        priority=data.get("priority", 0),
        path=data.get("path"),
        description=data.get("description"),
        tracking=data.get("tracking"),
        pagination=data.get("pagination"),
        other=data.get("other"),
    )
