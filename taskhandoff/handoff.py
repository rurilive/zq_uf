"""任务迁移交接：检查点 + 双写幂等确认 + 失败回滚。"""

_hosts = {}
_states = {}
_faults = {}


class _TaskState:
    """模拟任务状态：written 已写结果数，checkpoint 检查点位移，
    source 待写结果总量（要追上的前沿），sink 幂等键到结果的存储。"""

    def __init__(self):
        self.written = 0
        self.checkpoint = 0
        self.source = 0
        self.sink = {}
        self.read_only = False
        self.last_done = None


def _state(task_id):
    return _states.setdefault(task_id, _TaskState())


def _idem_key(task_id, seq):
    return "%s#%d" % (task_id, seq)


def _payload(task_id, seq):
    return "%s-result-%d" % (task_id, seq)


def _outcome(task_id, from_host, to_host, stage, phases, hits, reason, state):
    return {
        "task_id": task_id,
        "from": from_host,
        "to": to_host,
        "stage": stage,
        "phases": list(phases),
        "idempotent_hits": hits,
        "rollback_reason": reason,
        "checkpoint_offset": state.checkpoint,
    }


def _rollback(task_id, from_host, to_host, state, phases, hits, reason):
    state.read_only = False
    phases.append("rollback")
    return _outcome(task_id, from_host, to_host, "rolled_back",
                    phases, hits, reason, state)


def handoff(task_id, from_host, to_host):
    """旧任务落检查点并只读，新任务从检查点继续双写，追上后停旧任务。

    任一步失败回滚到旧任务继续，已写结果不回退；相同 (from, to)
    的重复交接幂等命中，返回 stage="duplicate"。
    """
    state = _state(task_id)
    current = _hosts.get(task_id)

    if state.last_done == (from_host, to_host):
        return _outcome(task_id, from_host, to_host, "duplicate",
                        [], 0, None, state)

    if current is not None and current != from_host:
        return _outcome(task_id, from_host, to_host, "rolled_back", [], 0,
                        "from_host_mismatch: task on %s" % current, state)

    if current is None:
        _hosts[task_id] = from_host

    phases = []
    hits = 0

    phases.append("checkpoint")
    if _faults.get(task_id) == "checkpoint":
        return _rollback(task_id, from_host, to_host, state, phases, hits,
                         "checkpoint_failed")
    state.read_only = True

    phases.append("dual_write")
    cursor = state.checkpoint
    frontier = state.source
    while cursor < frontier:
        key = _idem_key(task_id, cursor)
        if key in state.sink:
            hits += 1
        else:
            state.sink[key] = _payload(task_id, cursor)
        cursor += 1
    if _faults.get(task_id) == "dual_write":
        return _rollback(task_id, from_host, to_host, state, phases, hits,
                         "dual_write_failed")

    phases.append("catch_up")
    caught_up = cursor >= frontier and all(
        _idem_key(task_id, seq) in state.sink for seq in range(frontier))
    if not caught_up or _faults.get(task_id) == "catch_up":
        return _rollback(task_id, from_host, to_host, state, phases, hits,
                         "catch_up_failed")

    phases.append("stop_old")
    if _faults.get(task_id) == "stop_old":
        return _rollback(task_id, from_host, to_host, state, phases, hits,
                         "stop_old_failed")
    state.read_only = False
    state.written = frontier
    state.checkpoint = frontier
    _hosts[task_id] = to_host
    state.last_done = (from_host, to_host)
    return _outcome(task_id, from_host, to_host, "started",
                    phases, hits, None, state)


def running(task_id):
    return _hosts.get(task_id)
