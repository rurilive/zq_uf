"""步骤、分支、处理器注册表、条件 DSL 与序列化。"""

import json

from .errors import FlowDefinitionError

STATE_KEY = "__flowstep__"

_MISSING = object()

_OPERATORS = {
    "eq": lambda actual, expected: actual == expected,
    "ne": lambda actual, expected: actual != expected,
    "gt": lambda actual, expected: actual > expected,
    "ge": lambda actual, expected: actual >= expected,
    "lt": lambda actual, expected: actual < expected,
    "le": lambda actual, expected: actual <= expected,
    "in": lambda actual, expected: actual in expected,
    "not_in": lambda actual, expected: actual not in expected,
    "contains": lambda actual, expected: expected in actual,
    "exists": lambda actual, expected: actual is not _MISSING,
    "truthy": lambda actual, expected: bool(actual),
}


def validate_condition(condition):
    """递归校验条件表达式，保证流程定义是可序列化的纯数据。"""
    if condition is None:
        return None
    if not isinstance(condition, dict):
        raise FlowDefinitionError("条件必须是字典或 None: %r" % (condition,))
    if "all" in condition:
        items = condition["all"]
        if not isinstance(items, list) or not items:
            raise FlowDefinitionError("all 条件必须是非空列表")
        return {"all": [validate_condition(item) for item in items]}
    if "any" in condition:
        items = condition["any"]
        if not isinstance(items, list) or not items:
            raise FlowDefinitionError("any 条件必须是非空列表")
        return {"any": [validate_condition(item) for item in items]}
    if "not" in condition:
        return {"not": validate_condition(condition["not"])}
    if "field" not in condition:
        raise FlowDefinitionError("条件缺少 field: %r" % (condition,))
    op = condition.get("op", "eq")
    if op not in _OPERATORS:
        raise FlowDefinitionError("不支持的条件操作符: %s" % op)
    return dict(condition)


def _lookup(context, field):
    current = context
    for part in str(field).split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def evaluate_condition(condition, context):
    """按上下文求值条件，缺字段的比较一律为 False。"""
    if condition is None:
        return True
    if "all" in condition:
        return all(evaluate_condition(item, context) for item in condition["all"])
    if "any" in condition:
        return any(evaluate_condition(item, context) for item in condition["any"])
    if "not" in condition:
        return not evaluate_condition(condition["not"], context)
    actual = _lookup(context, condition["field"])
    op = condition.get("op", "eq")
    if op == "exists":
        return actual is not _MISSING
    if actual is _MISSING:
        return False
    return _OPERATORS[op](actual, condition.get("value"))


class Registry:
    """步骤名到处理器函数的映射，使流程定义可以只存字符串。"""

    _default = None

    def __init__(self):
        self._handlers = {}
        self._reverse = {}

    @classmethod
    def default(cls):
        if cls._default is None:
            cls._default = Registry()
        return cls._default

    def register(self, func=None, name=None):
        if func is None:
            return lambda wrapped: self.register(wrapped, name)
        key = name or func.__name__
        if key in self._handlers and self._handlers[key] is not func:
            raise FlowDefinitionError("步骤名已被注册: %s" % key)
        self._handlers[key] = func
        self._reverse[getattr(func, "__func__", func)] = key
        return func

    def get(self, name):
        if name not in self._handlers:
            raise FlowDefinitionError("未注册的步骤处理器: %s" % name)
        return self._handlers[name]

    def name_of(self, func):
        key = self._reverse.get(getattr(func, "__func__", func))
        if key is None:
            raise FlowDefinitionError(
                "处理器 %s 未注册，无法序列化流程" % getattr(func, "__name__", func)
            )
        return key


registry = Registry.default()


class Step:
    """可编排步骤：声明输入、输出、幂等性与可选条件。"""

    def __init__(self, name, handler=None, inputs=(), outputs=(),
                 idempotent=False, when=None, legacy=False):
        if not name:
            raise FlowDefinitionError("步骤必须有非空 name")
        self.name = name
        self.handler = handler if handler is not None else name
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        self.idempotent = bool(idempotent)
        self.when = validate_condition(when)
        self.legacy = legacy

    def to_dict(self, reg=None):
        handler_key = self.handler
        if callable(handler_key):
            handler_key = (reg or Registry.default()).name_of(handler_key)
        data = {
            "step": self.name,
            "handler": handler_key,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "idempotent": self.idempotent,
        }
        if self.when is not None:
            data["when"] = self.when
        return data

    @classmethod
    def from_dict(cls, data):
        name = data.get("step") or data.get("name")
        if not name or "handler" not in data:
            raise FlowDefinitionError("步骤定义缺少 step/handler: %r" % (data,))
        return cls(
            name=name,
            handler=data["handler"],
            inputs=data.get("inputs", []),
            outputs=data.get("outputs", []),
            idempotent=data.get("idempotent", False),
            when=data.get("when"),
        )


class Branch:
    """条件分支：按顺序匹配第一个 when 命中的分支，None 为默认分支。"""

    def __init__(self, choices):
        normalized = []
        saw_default = False
        for choice in choices:
            if isinstance(choice, dict):
                when = choice.get("when")
                steps = choice["steps"]
            else:
                when, steps = choice
            if when is None:
                if saw_default:
                    raise FlowDefinitionError("一个 Branch 只能有一个默认分支")
                saw_default = True
            normalized.append((validate_condition(when), list(steps)))
        self.choices = normalized

    def to_dict(self, reg=None):
        items = []
        for when, steps in self.choices:
            item = {"steps": [node_to_dict(node, reg) for node in steps]}
            if when is not None:
                item["when"] = when
            items.append(item)
        return {"branch": items}


def node_to_dict(node, reg=None):
    if isinstance(node, Step):
        return node.to_dict(reg)
    if isinstance(node, Branch):
        return node.to_dict(reg)
    if isinstance(node, dict):
        if "branch" in node:
            return Branch(node["branch"]).to_dict(reg)
        return Step.from_dict(node).to_dict(reg)
    if callable(node):
        return Step(node.__name__, handler=node, legacy=True).to_dict(reg)
    raise FlowDefinitionError("无法识别的流程节点: %r" % (node,))


def to_dict(flow):
    """把流程转成 JSON 兼容的纯数据。"""
    return {"version": 1, "nodes": [node_to_dict(node) for node in flow]}


def _node_from_dict(data):
    if "branch" in data:
        choices = []
        for item in data["branch"]:
            choices.append({
                "when": item.get("when"),
                "steps": [_node_from_dict(child) for child in item["steps"]],
            })
        return Branch(choices)
    return Step.from_dict(data)


def from_dict(data):
    """从纯数据重建流程，处理器按名字在运行时经注册表解析。"""
    if isinstance(data, dict) and "nodes" in data:
        nodes = data["nodes"]
    elif isinstance(data, list):
        nodes = data
    else:
        raise FlowDefinitionError("流程数据格式不合法: %r" % (data,))
    return [_node_from_dict(item) for item in nodes]


def dumps(flow):
    return json.dumps(to_dict(flow), ensure_ascii=False, indent=2)


def loads(text):
    return from_dict(json.loads(text))
