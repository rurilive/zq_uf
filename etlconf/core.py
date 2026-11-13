"""配置驱动的转换层：加载 plan、校验冲突、按序执行可组合步骤。"""
import copy

from . import steps as _steps

DEFAULT_STEPS = ("strip", "cast", "fill", "rename", "derive")
CAST_TYPES = ("str", "int", "float")
_DICT_SECTIONS = ("cast", "fill", "rename", "derive")
_LIST_SECTIONS = ("strip", "nulls", "steps")


class PlanError(ValueError):
    """plan 加载期的配置冲突或非法定义。"""


class Plan:
    """编译后的 plan，可复用于多批数据。"""

    def __init__(self, step_names, null_tokens, strip, cast, fill, rename, derive):
        self.step_names = step_names
        self.null_tokens = null_tokens
        self.strip = strip
        self.cast = cast
        self.fill = fill
        self.rename = rename
        self.derive = derive

    def apply(self, row):
        row = dict(row)
        for name in self.step_names:
            _STEP_FUNCS[name](self, row)
        return row


def compile_plan(plan, source=None):
    """把 plan 字典编译成 Plan；同名字段的规则冲突在此刻抛 PlanError。"""
    if isinstance(plan, Plan):
        return plan
    if not isinstance(plan, dict):
        raise PlanError("plan 必须是 dict")
    if source is None:
        source = plan.get("source")
    merged = _apply_override(plan, source)
    return _build(merged)


def transform(rows, plan, source=None):
    """按 plan 转换每一行；plan 可以是 dict 或编译后的 Plan。"""
    compiled = compile_plan(plan, source)
    return [compiled.apply(row) for row in rows]


def _apply_override(plan, source):
    """基础 plan 之上合并 overrides[source]：字典段按键覆盖，列表段整体替换。"""
    base = {key: copy.deepcopy(value) for key, value in plan.items()
            if key not in ("overrides", "source")}
    overrides = plan.get("overrides", {})
    if not isinstance(overrides, dict):
        raise PlanError("overrides 必须是 dict")
    if source is None or source not in overrides:
        return base
    fragment = overrides[source]
    if not isinstance(fragment, dict):
        raise PlanError("overrides[{!r}] 必须是 dict".format(source))
    for key, value in fragment.items():
        if key in _DICT_SECTIONS:
            section = dict(base.get(key, {}))
            section.update(value)
            base[key] = section
        elif key in _LIST_SECTIONS:
            base[key] = copy.deepcopy(value)
        else:
            raise PlanError("overrides 不支持覆盖 {!r}".format(key))
    return base


def _build(cfg):
    step_names = tuple(cfg.get("steps", DEFAULT_STEPS))
    unknown = [name for name in step_names if name not in _STEP_FUNCS]
    if unknown:
        raise PlanError("未知步骤: {}".format(unknown))

    nulls = cfg.get("nulls", [])
    strip = list(cfg.get("strip", []))
    cast = dict(cfg.get("cast", {}))
    fill = dict(cfg.get("fill", {}))
    rename = dict(cfg.get("rename", {}))
    derive = dict(cfg.get("derive", {}))

    for field, type_name in cast.items():
        if type_name not in CAST_TYPES:
            raise PlanError("字段 {!r} 声明了未知类型 {!r}".format(field, type_name))

    targets = list(rename.values())
    if len(set(targets)) != len(targets):
        raise PlanError("rename 目标字段冲突: {}".format(sorted(targets)))

    clash = sorted(set(derive) & set(targets))
    if clash:
        raise PlanError("派生字段与 rename 目标冲突: {}".format(clash))

    tokens = ("",) + tuple(nulls)
    typed_fill = {}
    for field, value in fill.items():
        if _steps.is_null(value, tokens):
            raise PlanError("字段 {!r} 的填充值不能是空值".format(field))
        if field in cast:
            try:
                value = _steps.cast_value(value, cast[field])
            except ValueError as exc:
                raise PlanError(
                    "字段 {!r} 填充值与类型 {!r} 冲突".format(field, cast[field])) from exc
        typed_fill[field] = value

    compiled_derive = {
        field: _steps.compile_expression(expr, field) for field, expr in derive.items()
    }
    return Plan(step_names, tokens, strip, cast, typed_fill, rename, compiled_derive)


def _run_strip(plan, row):
    for field in plan.strip:
        value = row.get(field)
        if isinstance(value, str):
            row[field] = value.strip()


def _run_cast(plan, row):
    for field, type_name in plan.cast.items():
        if field not in row:
            continue
        value = row[field]
        row[field] = None if _steps.is_null(value, plan.null_tokens) \
            else _steps.cast_value(value, type_name)


def _run_fill(plan, row):
    for field, value in plan.fill.items():
        if field not in row or _steps.is_null(row[field], plan.null_tokens):
            row[field] = value


def _run_rename(plan, row):
    for old, new in plan.rename.items():
        if old in row:
            row[new] = row.pop(old)


def _run_derive(plan, row):
    for field, tree in plan.derive.items():
        row[field] = _steps.eval_expression(tree, row)


_STEP_FUNCS = {
    "strip": _run_strip,
    "cast": _run_cast,
    "fill": _run_fill,
    "rename": _run_rename,
    "derive": _run_derive,
}
