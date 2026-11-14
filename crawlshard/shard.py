"""分片双活：租约 + 单调任期。

主机持租约写入，备机只读；租约到期后备机竞选，任期单调递增；
旧任期与少数派写入被拒并记录；切换后从最后确认位点续写。
"""

import time

LEASE_SECONDS = 5.0

_owners = {}
_partitions = {}


def _new_state(shard, primary, lease_until):
    return {
        "shard": shard,
        "term": 0,
        "primary": primary,
        "lease_until": lease_until,
        "standbys": set(),
        "demoted": {},
        "offset": 0,
        "log": [],
        "rejected": 0,
        "events": [],
    }


def _members(st):
    return {st["primary"]} | set(st["standbys"]) | set(st["demoted"])


def _quorum(st):
    return len(_members(st)) // 2 + 1


def _reachable(shard, st, node):
    groups = _partitions.get(shard)
    if not groups:
        return _members(st)
    for group in groups:
        if node in group:
            return set(group) & _members(st)
    return {node}


def _has_quorum(shard, st, node):
    return len(_reachable(shard, st, node)) >= _quorum(st)


def _view(st, node, role):
    return {
        "shard": st["shard"],
        "node": node,
        "term": st["term"],
        "role": role,
        "offset": st["offset"],
        "rejected": st["rejected"],
    }


def _reject(st, node, role, reason):
    st["rejected"] += 1
    st["events"].append(
        {"type": "reject", "reason": reason, "node": node, "term": st["term"]}
    )
    return {
        "shard": st["shard"],
        "node": node,
        "written": False,
        "term": st["term"],
        "role": role,
        "reason": reason,
        "offset": st["offset"],
        "rejected": st["rejected"],
    }


def _confirm(st, node, role, record):
    st["log"].append({"term": st["term"], "record": record})
    st["offset"] += 1
    return {
        "shard": st["shard"],
        "node": node,
        "written": True,
        "term": st["term"],
        "role": role,
        "offset": st["offset"],
        "rejected": st["rejected"],
    }


def elect(shard, node):
    """首个节点成主；租约有效内他人为备机；租约到期后竞选，任期 +1。"""
    now = time.monotonic()
    st = _owners.get(shard)
    if st is None:
        st = _new_state(shard, node, now + LEASE_SECONDS)
        _owners[shard] = st
        return _view(st, node, "primary")
    if now < st["lease_until"]:
        if node == st["primary"]:
            st["lease_until"] = now + LEASE_SECONDS
            return _view(st, node, "primary")
        st["standbys"].add(node)
        st["demoted"].pop(node, None)
        return _view(st, node, "standby")
    if not _has_quorum(shard, st, node):
        st["standbys"].add(node)
        st["events"].append(
            {"type": "campaign_minority", "node": node, "term": st["term"]}
        )
        return _view(st, node, "standby")
    old = st["primary"]
    if old != node:
        st["demoted"][old] = st["term"]
    st["standbys"].discard(node)
    st["demoted"].pop(node, None)
    st["term"] += 1
    st["primary"] = node
    st["lease_until"] = now + LEASE_SECONDS
    st["events"].append(
        {
            "type": "failover",
            "from": old,
            "to": node,
            "term": st["term"],
            "offset": st["offset"],
        }
    )
    return _view(st, node, "primary")


def write(shard, node, record):
    """主持租约且有法定人数才确认写入；备机、旧任期、少数派拒绝。"""
    now = time.monotonic()
    st = _owners.get(shard)
    if st is None:
        st = _new_state(shard, None, 0.0)
        _owners[shard] = st
    if node == st["primary"]:
        if now >= st["lease_until"]:
            return _reject(st, node, "stale", "lease_expired")
        if not _has_quorum(shard, st, node):
            return _reject(st, node, "minority", "minority_partition")
        return _confirm(st, node, "primary", record)
    if node in st["demoted"]:
        return _reject(st, node, "stale", "stale_term")
    if node in st["standbys"]:
        return _reject(st, node, "standby", "standby_read_only")
    primary = st["primary"]
    if (
        primary is not None
        and now < st["lease_until"]
        and _has_quorum(shard, st, primary)
    ):
        return _confirm(st, node, "forwarded", record)
    return _reject(st, node, "unknown", "no_primary")


def status(shard):
    """输出任期、主备角色、位点、拒绝写次数和事件。"""
    st = _owners.get(shard)
    if st is None:
        return {
            "shard": shard,
            "term": 0,
            "primary": None,
            "standbys": [],
            "offset": 0,
            "rejected": 0,
            "events": [],
        }
    return {
        "shard": shard,
        "term": st["term"],
        "primary": st["primary"],
        "standbys": sorted(st["standbys"]),
        "offset": st["offset"],
        "rejected": st["rejected"],
        "events": list(st["events"]),
    }


def expire_lease(shard):
    """模拟主机失联：租约立即到期。"""
    st = _owners.get(shard)
    if st is not None:
        st["lease_until"] = 0.0


def set_partition(shard, groups):
    """模拟脑裂：groups 为互不连通的节点组。"""
    _partitions[shard] = [set(group) for group in groups]


def heal_partition(shard):
    _partitions.pop(shard, None)
