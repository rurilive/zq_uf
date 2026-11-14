"""分片双活：租约 + 单调任期 + 多数派心跳 + 位点续写。

未配置集群的分片保持 term=0 的引导语义：任何节点都能写，方便单测
与历史调用方；一旦通过 configure 配置成员，即进入强制模式：
只有持有效租约的多数派主机可以写，备机只读，旧任期写入被拒。
"""

import threading
import time

_lock = threading.RLock()

# shard -> 分片状态，沿用历史变量名供测试清理。
_owners = {}

# 仅测试/demo 使用的可调时钟偏移，不影响生产调用。
_clock_offset = 0.0


def _now():
    return time.monotonic() + _clock_offset


def advance(seconds):
    """推进虚拟时钟，供测试与 demo 模拟租约到期。"""
    global _clock_offset
    with _lock:
        _clock_offset += float(seconds)


def _get(shard):
    state = _owners.get(shard)
    if state is None:
        state = {
            "members": (),
            "term": 0,
            "leader": None,
            "lease_until": 0.0,
            "lease_ttl": 10.0,
            "position": 0,
            "rejected_writes": 0,
            "last_views": {},
        }
        _owners[shard] = state
    return state


def configure(shard, members, lease_ttl=10.0):
    """配置分片成员与租约时长；重复配置只调整成员与 ttl，不清任期。"""
    members = tuple(members)
    with _lock:
        state = _get(shard)
        state["members"] = members
        state["lease_ttl"] = float(lease_ttl)
        state.setdefault("last_views", {})
    return status(shard)


def _quorum(state):
    return len(state["members"]) // 2 + 1


def _reachable(state, node):
    """节点最近一次心跳看到的成员数是否达到多数派。"""
    view = state["last_views"].get(node)
    if view is None:
        view = state["members"]
    return len(set(view)) >= _quorum(state)


def _role(state, node):
    if state["leader"] is None:
        return "standby"
    if node == state["leader"]:
        return "leader" if _now() < state["lease_until"] else "unreachable"
    return "standby"


def elect(shard, node):
    """竞选主机。租约有效时拒绝抢占，租约到期后多数派可当选，任期单调递增。"""
    with _lock:
        state = _get(shard)
        now = _now()

        if not state["members"]:
            # term=0 引导期，保持历史宽松语义。
            state["leader"] = node
            return {
                "shard": shard,
                "node": node,
                "term": 0,
                "elected": True,
                "role": "leader",
            }

        if state["leader"] is not None and now < state["lease_until"]:
            return {
                "shard": shard,
                "node": node,
                "term": state["term"],
                "elected": False,
                "role": _role(state, node),
                "reason": "lease_held",
            }

        if not _reachable(state, node):
            return {
                "shard": shard,
                "node": node,
                "term": state["term"],
                "elected": False,
                "role": "standby",
                "reason": "minority_partition",
            }

        state["term"] += 1
        state["leader"] = node
        state["lease_until"] = now + state["lease_ttl"]
        state["last_views"][node] = state["members"]
        return {
            "shard": shard,
            "node": node,
            "term": state["term"],
            "elected": True,
            "role": "leader",
        }


def heartbeat(shard, node, seen=None):
    """上报心跳并携带可见成员；主机在多数派可见时续租。"""
    with _lock:
        state = _get(shard)
        if state["members"]:
            view = tuple(state["members"]) if seen is None else tuple(seen)
            state["last_views"][node] = view

        renewed = False
        if (
            state["members"]
            and node == state["leader"]
            and _now() < state["lease_until"]
            and _reachable(state, node)
        ):
            state["lease_until"] = _now() + state["lease_ttl"]
            renewed = True

        return {
            "shard": shard,
            "node": node,
            "term": state["term"],
            "role": _role(state, node),
            "lease_renewed": renewed,
            "quorum": _reachable(state, node) if state["members"] else True,
        }


def _reject(state, reason):
    state["rejected_writes"] += 1
    return reason


def write(shard, node, record, term=None):
    """持有效租约的多数派主机写入；旧任期、备机、少数派、租约过期均拒绝。"""
    with _lock:
        state = _get(shard)
        now = _now()

        if not state["members"]:
            # term=0 引导期，保持历史宽松语义。
            state["leader"] = node
            return {
                "shard": shard,
                "node": node,
                "term": 0,
                "written": True,
                "role": "leader",
                "position": state["position"],
                "rejected_writes": state["rejected_writes"],
            }

        if term is not None and term < state["term"]:
            reason = _reject(state, "stale_term")
        elif node != state["leader"] or (
            term is not None and term != state["term"]
        ):
            reason = _reject(state, "not_leader")
        elif not _reachable(state, node):
            reason = _reject(state, "minority_partition")
        elif now >= state["lease_until"]:
            reason = _reject(state, "lease_expired")
        else:
            reason = None

        if reason is not None:
            return {
                "shard": shard,
                "node": node,
                "term": state["term"],
                "written": False,
                "role": _role(state, node),
                "reason": reason,
                "position": state["position"],
                "rejected_writes": state["rejected_writes"],
            }

        position = record.get("position") if isinstance(record, dict) else None
        if position is None:
            position = state["position"] + 1
        elif position <= state["position"]:
            reason = _reject(state, "stale_position")
            return {
                "shard": shard,
                "node": node,
                "term": state["term"],
                "written": False,
                "role": "leader",
                "reason": reason,
                "position": state["position"],
                "rejected_writes": state["rejected_writes"],
            }

        state["position"] = position
        state["lease_until"] = now + state["lease_ttl"]
        return {
            "shard": shard,
            "node": node,
            "term": state["term"],
            "written": True,
            "role": "leader",
            "position": position,
            "rejected_writes": state["rejected_writes"],
        }


def status(shard, node=None):
    """输出任期、角色、租约、位点与拒绝写次数。"""
    with _lock:
        state = _get(shard)
        return {
            "shard": shard,
            "term": state["term"],
            "leader": state["leader"],
            "role": _role(state, node) if node is not None else None,
            "lease_valid": _now() < state["lease_until"],
            "position": state["position"],
            "rejected_writes": state["rejected_writes"],
            "quorum": _quorum(state),
            "members": list(state["members"]),
        }
