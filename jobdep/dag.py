"""DAG 执行、失败传播、局部重跑与依赖热更新。

接口（保持 ``run(dag)`` / ``rerun(failed)``）：

- :func:`run`：校验 DAG，按入度拓扑执行；上游失败后下游标记 ``skipped``
  并记录跳过原因；节点状态与跳过原因落盘，失败时把原异常附加 ``state``
  后重新抛出（兼容旧的“失败即停”语义）。
- :func:`rerun`：从落盘状态读取 ``failed`` / ``skipped`` 集合，只重跑这
  两类节点，``done`` 节点复用不执行；跳过节点不占用执行位。

纯标准库实现，执行仍为线性（单执行位）。
"""

import json
import os
import uuid

# ---- 节点状态 ----
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

# ---- 跳过原因 ----
REASON_UPSTREAM_FAILED = "upstream_failed"
REASON_ABORTED = "run_aborted"

TERMINAL = (DONE, FAILED, SKIPPED)
STATE_FILENAME = "state.json"

# run 时注册的 DAG（函数不可 JSON 化，路径重跑时从这里取）。
_DAG_REGISTRY = {}


class DagValidationError(ValueError):
    """DAG 结构非法（重名、未知依赖、成环、冻结节点改边等）。"""


class RunState(dict):
    """节点名 -> 状态的映射（dict 子类，兼容旧的 ``state[name]`` 用法）。

    同时承载跳过原因、依赖边与落盘位置，供 :func:`rerun` 复用。
    """

    def __init__(self, run_id, deps, statuses, skip_reasons, state_dir,
                 state_path, dag=None, reruns=0):
        super().__init__(statuses)
        self.run_id = run_id
        self.deps = deps
        self.skip_reasons = dict(skip_reasons)
        self.state_dir = state_dir
        self.state_path = state_path
        self.dag = dag
        self.reruns = reruns

    # -- 查询 --
    def skipped_nodes(self):
        return sorted(n for n, s in self.items() if s == SKIPPED)

    def failed_nodes(self):
        return sorted(n for n, s in self.items() if s == FAILED)

    def done_nodes(self):
        return sorted(n for n, s in self.items() if s == DONE)

    def reason(self, name):
        """返回 ``(原因码, 触发节点名)``，未跳过返回 ``None``。"""
        return self.skip_reasons.get(name)

    # -- 运行中依赖热更新：先校验，挂起，当前节点结束后再热更 --
    def update_deps(self, add=(), remove=()):
        self._pending_update = (list(add), list(remove))

    # -- 落盘 --
    def payload(self):
        return {
            "version": 1,
            "run_id": self.run_id,
            "reruns": self.reruns,
            "deps": {k: sorted(v) for k, v in self.deps.items()},
            "statuses": dict(self),
            "skip_reasons": self.skip_reasons,
        }

    def persist(self):
        write_state_file(self.state_path, self.payload())

    @classmethod
    def from_payload(cls, payload, state_path, dag=None):
        return cls(
            run_id=payload["run_id"],
            deps={k: list(v) for k, v in payload["deps"].items()},
            statuses=dict(payload["statuses"]),
            skip_reasons=dict(payload.get("skip_reasons", {})),
            state_dir=os.path.dirname(os.path.abspath(state_path)),
            state_path=os.path.abspath(state_path),
            dag=dag,
            reruns=payload.get("reruns", 0),
        )


def write_state_file(path, payload):
    """原子写：同目录临时文件 + os.replace，避免半截状态文件。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, ".%s.tmp" % uuid.uuid4().hex)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def read_state_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---- DAG 规范化与校验 ----

def _build_graph(dag):
    """把入参 DAG 转成内部图，做一次性结构校验。"""
    steps = dag.get("steps") or []
    names, fns = [], {}
    for step in steps:
        name = step["name"]
        if name in fns:
            raise DagValidationError("duplicate step name: %r" % name)
        names.append(name)
        fns[name] = step["fn"]

    deps = {}
    for step in steps:
        d = step.get("deps") or []
        unknown = [u for u in d if u not in fns]
        if unknown:
            raise DagValidationError(
                "step %r depends on unknown node(s): %r" % (step["name"], unknown))
        deps[step["name"]] = list(d)

    _assert_acyclic(deps)
    return names, fns, deps


def _assert_acyclic(deps):
    """Kahn 校验：能消费完所有节点即无环。"""
    indeg = {n: len(d) for n, d in deps.items()}
    successors = {n: [] for n in deps}
    for n, ds in deps.items():
        for d in ds:
            successors[d].append(n)
    queue = sorted(n for n, k in indeg.items() if k == 0)
    seen = 0
    while queue:
        n = queue.pop(0)
        seen += 1
        for m in successors[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
        queue.sort()
    if seen != len(deps):
        raise DagValidationError("dependency cycle detected in DAG")


class _Engine:
    """单执行位（线性）拓扑引擎。

    只有真正执行函数的节点占用执行位；``skipped`` 节点直接传播，不占执行位。
    """

    def __init__(self, state, fns, participating=None):
        self.state = state
        self.fns = fns
        self.deps = state.deps
        # participating：本次实际参与的节点；rerun 时仅失败/跳过集合，
        # 其余 done 节点视为已满足的外部前置。
        self.participating = set(participating if participating is not None
                                 else state.keys())
        self.order = list(state.keys())  # DAG 声明顺序，就绪节点按此出队
        self.indeg = {}
        for n in self.participating:
            self.indeg[n] = sum(1 for d in self.deps[n]
                                if d in self.participating)

    # -- 主循环 --
    def execute(self):
        while True:
            ready = [
                n for n in self.order
                if n in self.participating
                and self.state[n] == PENDING and self.indeg[n] == 0
            ]
            if not ready:
                break
            name = ready[0]

            self.state[name] = RUNNING
            self.state.persist()
            try:
                self.fns[name](self.state)
                # 热更新在“当前节点函数返回、落终态之前”应用：校验先于生效，
                # 此时 name 仍处于 running（冻结），不能作为边的落点。
                pending_update = self.state.__dict__.pop("_pending_update", None)
                if pending_update is not None:
                    try:
                        self._apply_update(name, *pending_update)
                    except DagValidationError as exc:
                        # 校验失败：更新不生效，当前节点函数本身已成功，
                        # 中止本次运行并跳过剩余未决节点。
                        self.state[name] = DONE
                        self.state.persist()
                        self._abort()
                        exc.state = self.state
                        raise
            except DagValidationError:
                raise
            except Exception as exc:  # 失败即停：下游传播 + 其余未决跳过
                self.state[name] = FAILED
                self.state.persist()
                self._propagate(name, name)
                self._abort()
                exc.state = self.state
                raise
            self.state[name] = DONE
            self.state.persist()
            self._release(name)
        return self.state

    def _abort(self):
        """失败即停：所有仍未决的节点标记跳过（原因 run_aborted）。"""
        for n in self.order:
            if n in self.participating and self.state[n] == PENDING:
                self.state[n] = SKIPPED
                self.state.skip_reasons[n] = [REASON_ABORTED, None]
        self.state.persist()

    def _release(self, name):
        """终态节点释放后继入度；跳过的后继不参与就绪竞争。"""
        for m in self._successors(name):
            if m in self.participating and self.state[m] == PENDING:
                self.indeg[m] -= 1

    def _propagate(self, failed, current):
        """BFS 把 current 的下游 pending 节点标记跳过，跳过不占执行位。"""
        for m in self._successors(current):
            if m in self.participating and self.state[m] == PENDING:
                self.state[m] = SKIPPED
                self.state.skip_reasons[m] = [REASON_UPSTREAM_FAILED, failed]
                self.state.persist()
                self._propagate(failed, m)

    def _successors(self, name):
        # 按 DAG 声明顺序遍历，保证传播结果稳定。
        return [m for m in self.order if name in self.deps[m]]

    # -- 热更新 --
    # 约定：元组 (u, v) 表示有向边 u -> v（u 先于 v）；内部 deps[v] 存 v 的
    # 前驱，因此加边即 deps[v].append(u)，v 的入度 +1。
    def _apply_update(self, would_run, add, remove):
        for u, v in add:
            self._check_endpoints(u, v, would_run)
            if u in self.deps[v]:
                continue  # 幂等
            tentative = {k: list(ds) for k, ds in self.deps.items()}
            tentative[v].append(u)
            _assert_acyclic(tentative)
            self.deps[v].append(u)
            if self.state[v] == PENDING and v in self.participating:
                self.indeg[v] += 1
            # 新边的上游若已失败/跳过，下游立即补传播。
            if self.state[u] in (FAILED, SKIPPED) and self.state[v] == PENDING:
                root = (self.state.skip_reasons.get(u) or [None, u])[1] or u
                self.state[v] = SKIPPED
                self.state.skip_reasons[v] = [REASON_UPSTREAM_FAILED, root]
                self.state.persist()
                self._propagate(root, v)
        for u, v in remove:
            self._check_known(u, v)
            if u in self.deps[v]:
                self.deps[v].remove(u)
                if self.state[v] == PENDING and v in self.participating:
                    self.indeg[v] -= 1
        self.state.persist()

    def _check_known(self, u, v):
        if u not in self.deps or v not in self.deps:
            raise DagValidationError(
                "dependency update references unknown node(s): %r -> %r" % (u, v))

    def _check_endpoints(self, u, v, running):
        self._check_known(u, v)
        # 边只允许落在未决节点上；运行中/已终态节点已冻结。
        if v == running or self.state[v] in TERMINAL:
            raise DagValidationError(
                "cannot change dependency onto frozen/terminal node %r" % v)


# ---- 顶层接口 ----

def run(dag, state_dir=None):
    """按入度拓扑执行 DAG。

    参数:
        dag: ``{"steps": [{"name", "fn", "deps?": [...]}]}``；无 ``deps``
            时退化为按列表顺序执行（兼容旧用法）。
        state_dir: 状态落盘目录，默认 ``./.jobdep/<run_id>``。

    返回: :class:`RunState`（dict 子类，含每个节点的状态）。
    抛出: 节点函数异常原样抛出，异常对象带 ``state`` 属性；结构非法抛
        :class:`DagValidationError`。
    """
    names, fns, deps = _build_graph(dag)
    run_id = uuid.uuid4().hex
    if state_dir is None:
        state_dir = os.path.join(".jobdep", run_id)
    state_dir = os.path.abspath(state_dir)
    state_path = os.path.join(state_dir, STATE_FILENAME)

    statuses = {n: PENDING for n in names}
    state = RunState(run_id, deps, statuses, {}, state_dir, state_path, dag=dag)
    state.persist()  # 先落盘：即使进程中途崩溃也留有状态文件

    _DAG_REGISTRY[state_path] = dag
    return _Engine(state, fns).execute()


def rerun(failed):
    """只重跑 failed/skipped 节点，done 节点复用不执行。

    参数:
        failed: 上次 :func:`run` 返回的 :class:`RunState`（或异常上的
            ``exc.state``），亦可是状态文件路径。重跑集合与状态一律以
            落盘 JSON 为准，内存对象被改过也不影响。

    返回: 合并后的新 :class:`RunState`，并回写同一个状态文件。
    """
    if isinstance(failed, RunState):
        state_path = failed.state_path
        dag = failed.dag or _DAG_REGISTRY.get(state_path)
    else:
        state_path = os.path.abspath(failed)
        dag = _DAG_REGISTRY.get(state_path)

    payload = read_state_file(state_path)  # 只读失败和跳过集合的来源
    if dag is None:
        raise DagValidationError(
            "no DAG registered for %s in this process; pass the RunState "
            "returned by run()" % state_path)

    names, fns, deps = _build_graph(dag)
    # 以最新注册的 DAG 边为准，但保留落盘状态（函数修复后重跑的场景）。
    state = RunState.from_payload(payload, state_path, dag=dag)
    state.deps = deps
    for n in names:
        state.setdefault(n, PENDING)

    targets = {n for n in names
               if state[n] in (FAILED, SKIPPED)}  # 仅失败 + 跳过
    for n in names:
        if n not in targets:
            if state[n] != DONE:
                state[n] = DONE  # 非目标节点一律视为可复用的已完成前置
        else:
            state[n] = PENDING
    state.skip_reasons = {n: r for n, r in state.skip_reasons.items()
                          if state[n] == SKIPPED}
    state.reruns += 1
    state.persist()

    _DAG_REGISTRY[state_path] = dag
    return _Engine(state, fns, participating=targets).execute()
