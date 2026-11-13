"""可编排步骤流引擎：断点续跑、幂等跳过、业务错误定位与条件分支。"""

from .errors import (
    BAD_RESULT,
    INPUT_MISSING,
    OUTPUT_MISSING,
    STEP_RAISED,
    FlowDefinitionError,
    StepError,
)
from .steps import (
    STATE_KEY,
    Branch,
    Registry,
    Step,
    dumps,
    evaluate_condition,
    from_dict,
    loads,
    registry,
    to_dict,
)

__all__ = [
    "run",
    "FlowResult",
    "Step",
    "Branch",
    "Registry",
    "registry",
    "fail",
    "to_dict",
    "from_dict",
    "dumps",
    "loads",
    "FlowDefinitionError",
    "StepError",
]


class FlowResult(dict):
    """执行结果：本身是上下文字典，同时携带结构化状态与错误信息。"""

    def __init__(self, data, state, ok, error=None):
        super().__init__(data)
        self.flow_state = state
        self.ok = ok
        self.error = error

    @property
    def failed_step(self):
        return self.error["step"] if self.error else None

    @property
    def code(self):
        return self.error["code"] if self.error else None

    @property
    def field(self):
        return self.error["field"] if self.error else None

    @property
    def completed_steps(self):
        return list(self.flow_state["completed"])


def _wrap_node(node, reg):
    if isinstance(node, (Step, Branch)):
        return node
    if isinstance(node, dict):
        if "branch" in node:
            return Branch(node["branch"])
        return Step.from_dict(node)
    if callable(node):
        return Step(getattr(node, "__name__", repr(node)), handler=node, legacy=True)
    raise FlowDefinitionError("无法识别的流程节点: %r" % (node,))


def _resolve_handler(step, reg):
    if callable(step.handler):
        return step.handler
    return reg.get(step.handler)


def _new_state():
    return {"completed": [], "failed": None, "runs": 0}


def run(flow, context, registry=None):
    """执行流程并返回 FlowResult（dict 子类）。

    - 入参 context 不会被修改，引擎在副本上工作。
    - 声明式步骤的检查点保存在保留键 __flowstep__ 中，随结果持久化即可断点续跑。
    - 仅由裸函数组成的旧版流程按原语义执行，不写检查点。
    """
    reg = registry or Registry.default()
    if isinstance(flow, dict) and "nodes" in flow:
        flow = from_dict(flow)
    nodes = [_wrap_node(node, reg) for node in flow]

    declarative = _validate_names(nodes)

    ctx = dict(context)
    state = _new_state()
    if declarative:
        saved = ctx.get(STATE_KEY)
        if isinstance(saved, dict) and isinstance(saved.get("completed"), list):
            state["runs"] = int(saved.get("runs", 0))
            state["completed"] = list(saved["completed"])
        ctx[STATE_KEY] = state

    state["runs"] += 1
    error = None
    try:
        for node in nodes:
            _walk(node, ctx, state, reg)
    except _Stop as stop:
        error = stop.payload
        state["failed"] = error

    return FlowResult(ctx, state, ok=error is None, error=error)


class _Stop(Exception):
    def __init__(self, payload):
        self.payload = payload


def _validate_names(nodes):
    seen = set()
    declarative = False

    def visit(node):
        nonlocal declarative
        if isinstance(node, Step):
            if node.legacy:
                return
            declarative = True
            if node.name in seen:
                raise FlowDefinitionError("步骤名重复: %s" % node.name)
            seen.add(node.name)
        elif isinstance(node, Branch):
            for _, steps in node.choices:
                for child in steps:
                    visit(child)

    for node in nodes:
        visit(node)
    return declarative


def _walk(node, ctx, state, reg):
    if isinstance(node, Branch):
        for when, steps in node.choices:
            if evaluate_condition(when, ctx):
                for child in steps:
                    _walk(_wrap_node(child, reg), ctx, state, reg)
                return
        return
    _run_step(node, ctx, state, reg)


def _fail(state, step, code, field=None, message=""):
    raise _Stop({
        "step": step.name,
        "code": code,
        "field": field,
        "message": message,
    })


def _run_step(step, ctx, state, reg):
    if step.legacy:
        handler = step.handler if callable(step.handler) else reg.get(step.handler)
        result = handler(ctx) or {}
        if not isinstance(result, dict):
            raise FlowDefinitionError("步骤 %s 必须返回字典" % step.name)
        ctx.update(result)
        return

    if not evaluate_condition(step.when, ctx):
        return

    if not step.idempotent and step.name in state["completed"]:
        return

    for field in step.inputs:
        if field not in ctx:
            _fail(state, step, INPUT_MISSING, field,
                  "缺少输入字段: %s" % field)

    handler = _resolve_handler(step, reg)
    try:
        result = handler(ctx)
    except StepError as exc:
        _fail(state, step, exc.code, exc.field, exc.message)
    except Exception as exc:  # 业务处理器未预期的异常也要能定位到步骤
        _fail(state, step, STEP_RAISED, None, "%s: %s" % (type(exc).__name__, exc))

    if result is None:
        result = {}
    if not isinstance(result, dict):
        _fail(state, step, BAD_RESULT, None, "步骤必须返回字典")

    for field in step.outputs:
        if field not in result:
            _fail(state, step, OUTPUT_MISSING, field,
                  "未产出声明的输出字段: %s" % field)

    ctx.update(result)
    if not step.idempotent and step.name not in state["completed"]:
        state["completed"].append(step.name)
