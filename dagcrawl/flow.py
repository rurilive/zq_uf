"""可编排的采集任务图：步骤声明依赖与产物，按入度调度执行。

流程定义（flow）是一个可 JSON 序列化的 dict：

    {
        "name": "site-a",
        "params": {"limit": 10},                 # 默认参数
        "sites": {"site_b": {"limit": 5}},       # 按站点覆盖参数
        "site": "site_b",                        # 选用哪个站点的覆盖（可选）
        "steps": [
            {"name": "list", "fn": "fetch_list", "provides": ["items"]},
            {"name": "detail", "fn": "fetch_detail",
             "needs": ["list"], "provides": ["details"]},
        ],
    }

"fn" 可以是可调用对象，也可以是 register() 注册过的名字（字符串），
后者保证整个 flow 可以 json.dumps 序列化、重启后复现。

旧格式（steps 为可调用对象列表）仍然支持，按声明顺序串成链执行。
"""


class FlowError(Exception):
    """流程定义或编排错误：环、未知依赖、未注册函数、缺产物等。"""


_REGISTRY = {}


def register(name=None, fn=None):
    """注册步骤函数，使流程可以用字符串引用函数，保持可序列化。

    支持三种用法：register("name", fn)、@register("name")、@register。
    """
    if callable(name) and fn is None:  # @register 无括号
        fn, name = name, None
    if fn is not None:
        _REGISTRY[name or fn.__name__] = fn
        return fn

    def deco(func):
        _REGISTRY[name or func.__name__] = func
        return func

    return deco


def for_site(flow, site):
    """返回选用了指定站点参数覆盖的流程副本，原流程不被修改。"""
    clone = dict(flow)
    clone["site"] = site
    return clone


def reset_state(flow):
    """清空执行状态，下次 run 从头完整执行（用于强制全量重跑）。"""
    flow["state"] = {}
    return flow


_STEP_KEYS = {"name", "fn", "needs", "provides"}


def _resolve_fn(ref, step_name):
    if callable(ref):
        return ref
    if isinstance(ref, str):
        if ref not in _REGISTRY:
            raise FlowError(
                "step %r references unregistered function %r" % (step_name, ref)
            )
        return _REGISTRY[ref]
    raise FlowError(
        "step %r has invalid 'fn' (expected callable or registered name)" % step_name
    )


def _normalize_steps(flow):
    raw = flow.get("steps")
    if not isinstance(raw, list) or not raw:
        raise FlowError("flow['steps'] must be a non-empty list")
    steps = []
    previous = None
    for index, item in enumerate(raw):
        if callable(item):
            # 旧格式：可调用对象列表，按声明顺序串成链。
            name = "%s#%d" % (getattr(item, "__name__", "step"), index)
            steps.append({
                "name": name,
                "fn": item,
                "needs": [previous] if previous else [],
                "provides": [],
            })
            previous = name
            continue
        if not isinstance(item, dict):
            raise FlowError(
                "flow['steps'][%d] must be a callable or a dict, got %r"
                % (index, type(item).__name__)
            )
        unknown = set(item) - _STEP_KEYS
        if unknown:
            raise FlowError(
                "step %r has unknown keys: %s"
                % (item.get("name", index), ", ".join(sorted(unknown)))
            )
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise FlowError("flow['steps'][%d] requires a non-empty string 'name'" % index)
        needs = item.get("needs", [])
        provides = item.get("provides", [])
        for key, value in (("needs", needs), ("provides", provides)):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise FlowError("step %r: %r must be a list of strings" % (name, key))
        steps.append({
            "name": name,
            "fn": _resolve_fn(item.get("fn"), name),
            "needs": list(needs),
            "provides": list(provides),
        })
    seen = set()
    for step in steps:
        if step["name"] in seen:
            raise FlowError("duplicate step name %r" % step["name"])
        seen.add(step["name"])
    for step in steps:
        for dep in step["needs"]:
            if dep not in seen:
                raise FlowError(
                    "step %r depends on unknown step %r" % (step["name"], dep)
                )
    return steps


def _topo_order(steps):
    """Kahn 算法按入度调度；同层按声明顺序，保证结果确定。"""
    indegree = {s["name"]: len(s["needs"]) for s in steps}
    dependents = {}
    for step in steps:
        for dep in step["needs"]:
            dependents.setdefault(dep, []).append(step["name"])
    ready = [s["name"] for s in steps if indegree[s["name"]] == 0]
    order = []
    while ready:
        name = ready.pop(0)
        order.append(name)
        for nxt in dependents.get(name, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    if len(order) != len(steps):
        stuck = sorted(n for n, d in indegree.items() if d > 0)
        raise FlowError("dependency cycle detected involving steps: %s" % ", ".join(stuck))
    return order


def _effective_params(flow):
    params = flow.get("params", {})
    if not isinstance(params, dict):
        raise FlowError("flow['params'] must be a dict")
    merged = dict(params)
    site = flow.get("site")
    if site is not None:
        sites = flow.get("sites") or {}
        if site not in sites:
            raise FlowError(
                "unknown site %r (defined sites: %s)"
                % (site, ", ".join(sorted(sites)) or "none")
            )
        override = sites[site]
        if not isinstance(override, dict):
            raise FlowError("flow['sites'][%r] must be a dict of param overrides" % site)
        merged.update(override)
    return merged


def run(flow, context):
    """按依赖顺序执行任务图，就地修改并返回 context。

    失败时原异常照常抛出，flow["state"] 记录各步骤状态
    （done/failed），与 context 一起 json.dumps 即可序列化；
    用同一个 flow 和 context 再次调用 run，已 done 的步骤跳过，
    只重跑失败步骤及其下游。
    """
    if not isinstance(flow, dict):
        raise FlowError("flow must be a dict")
    if context is None:
        context = {}
    steps = _normalize_steps(flow)
    order = _topo_order(steps)
    params = _effective_params(flow)
    if params:
        merged = dict(params)
        merged.update(context.get("params", {}))
        context["params"] = merged
    state = flow.setdefault("state", {})
    by_name = {s["name"]: s for s in steps}
    for name in order:
        if state.get(name) == "done":
            continue
        step = by_name[name]
        try:
            result = step["fn"](context)
        except Exception as exc:
            state[name] = "failed"
            if hasattr(exc, "add_note"):
                exc.add_note("dagcrawl step %r failed" % name)
            raise
        if result is None:
            state[name] = "failed"
            raise RuntimeError("step returned None")
        if result is not context:
            if isinstance(result, dict) and isinstance(context, dict):
                # 步骤返回新 dict 时合并回调用方的 dict，
                # 保证调用方持有的 context 始终累积最新产物。
                context.clear()
                context.update(result)
            else:
                context = result
        missing = [key for key in step["provides"] if key not in context]
        if missing:
            state[name] = "failed"
            raise FlowError(
                "step %r did not produce declared artifacts: %s"
                % (name, ", ".join(missing))
            )
        state[name] = "done"
    return context

