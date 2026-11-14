"""采集阶段屏障：按批次和分片协调，失败触发幂等补偿。"""

import threading
import time


class WaitResult(list):
    """等待结果。

    list 内容：成功或失败时为已到达分片，超时时为未到达分片。
    附带各分片状态、超时列表和补偿结果。
    """

    def __init__(self, shards, ok, status, timed_out, failed, compensation):
        super().__init__(shards)
        self.ok = ok
        self.status = status
        self.timed_out = timed_out
        self.failed = failed
        self.compensation = compensation


class _BatchState:
    def __init__(self):
        self.cond = threading.Condition()
        self.expected = None       # None 表示未登记期望分片，wait 立即返回
        self.arrived = {}          # shard -> ok，插入序即写入序
        self.compensations = {}    # shard -> 补偿回调
        self.compensated = False   # 补偿只执行一次（幂等）
        self.compensation_log = {} # shard -> 补偿返回值或错误


_arrived = {}


def _state(batch):
    return _arrived.setdefault(batch, _BatchState())


def expect(batch, *shards):
    """登记批次期望到达的分片，可传列表或多个参数。"""
    if len(shards) == 1 and not isinstance(shards[0], str):
        shards = tuple(shards[0])
    st = _state(batch)
    with st.cond:
        st.expected = set(shards)
        st.cond.notify_all()


def arrive(batch, shard, ok=True, compensate=None):
    """分片到达，返回已到达分片数。

    重复到达是 no-op（首次到达为准）。ok=False 时整批失败，
    立即按写入逆序执行已登记补偿，补偿只执行一次。
    """
    st = _state(batch)
    with st.cond:
        if shard not in st.arrived:
            st.arrived[shard] = bool(ok)
            if compensate is not None:
                st.compensations[shard] = compensate
            if not ok and not st.compensated:
                st.compensated = True
                for name in reversed(list(st.arrived)):
                    fn = st.compensations.get(name)
                    if fn is None:
                        continue
                    try:
                        st.compensation_log[name] = fn()
                    except Exception as exc:
                        st.compensation_log[name] = "error: %s" % exc
        st.cond.notify_all()
        return len(st.arrived)


def wait(batch, timeout=None):
    """等待批次全部期望分片到达。

    全部到达返回已到达分片；任一分片失败则整批失败并返回已到达分片；
    超时返回未到达分片。timeout 为 None 时一直等待。
    """
    st = _state(batch)
    deadline = None if timeout is None else time.monotonic() + timeout
    with st.cond:
        while True:
            done = st.expected is None or st.expected <= st.arrived.keys()
            failed = not all(st.arrived.values())
            if done or failed:
                break
            if deadline is None:
                st.cond.wait()
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            st.cond.wait(remaining)

        missing = sorted(st.expected - st.arrived.keys()) if st.expected is not None else []
        failed_shards = sorted(s for s, ok in st.arrived.items() if not ok)
        names = set(st.arrived)
        if st.expected:
            names |= st.expected
        status = {}
        for name in sorted(names):
            if name not in st.arrived:
                status[name] = "missing"
            elif st.arrived[name]:
                status[name] = "ok"
            else:
                status[name] = "failed"

        if done or failed_shards:
            contents = sorted(st.arrived)
            timed_out = []
        else:
            contents = missing
            timed_out = missing
        return WaitResult(
            contents,
            ok=done and not failed_shards,
            status=status,
            timed_out=timed_out,
            failed=failed_shards,
            compensation=dict(st.compensation_log),
        )
