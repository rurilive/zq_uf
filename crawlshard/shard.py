"""分片双活：租约 + 单调任期，主机持约写入，备机只读。"""

import time

LEASE_TTL = 5.0

_owners = {}

_down = set()
_isolated = set()


def _state(shard):
    return _owners.setdefault(shard, {
        "shard": shard,
        "term": 0,
        "primary": None,
        "lease_until": 0.0,
        "peers": [],
        "primary_terms": {},
        "log": [],
        "position": 0,
        "rejected": 0,
    })


def _alive(st):
    return [p for p in st["peers"] if p not in _down]


def _quorum(st):
    return len(_alive(st)) // 2 + 1


def _reachable(st, node):
    if node in _down:
        return 0
    if node in _isolated:
        return 1
    return 1 + sum(1 for p in _alive(st) if p != node and p not in _isolated)


def _lease_valid(st, now):
    return (st["primary"] is not None
            and st["primary"] not in _down
            and st["lease_until"] > now)


def _campaign(st, node, now):
    if node in _down or _reachable(st, node) < _quorum(st):
        return False
    if st["primary"] is not None:
        st["term"] += 1
    st["primary"] = node
    st["lease_until"] = now + LEASE_TTL
    st["primary_terms"][node] = st["term"]
    return True


def _view(st, node, role, **extra):
    out = {
        "shard": st["shard"],
        "node": node,
        "term": st["term"],
        "role": role,
        "position": st["position"],
        "rejected": st["rejected"],
    }
    out.update(extra)
    return out


def elect(shard, node):
    """竞选：无租约或租约到期/主机失联时，多数派可达者接任，任期单调递增。"""
    st = _state(shard)
    if node not in st["peers"]:
        st["peers"].append(node)
    now = time.monotonic()
    if st["primary"] == node and _lease_valid(st, now):
        st["lease_until"] = now + LEASE_TTL
        return _view(st, node, "primary")
    if not _lease_valid(st, now) and _campaign(st, node, now):
        return _view(st, node, "primary")
    return _view(st, node, "standby")


def _append(st, record, now):
    st["lease_until"] = now + LEASE_TTL
    st["log"].append(record)
    st["position"] += 1


def _reject(st, node, role, reason):
    st["rejected"] += 1
    return _view(st, node, role, written=False, reason=reason)


def write(shard, node, record):
    """写入：主机持租约且多数派可达才确认位点，旧任期与少数派拒绝并计数。"""
    st = _state(shard)
    now = time.monotonic()
    if st["primary"] == node and node not in _down:
        if st["lease_until"] <= now and not _campaign(st, node, now):
            return _reject(st, node, "primary", "minority")
        if _reachable(st, node) < _quorum(st):
            return _reject(st, node, "primary", "minority")
        _append(st, record, now)
        return _view(st, node, "primary", written=True)
    if node in st["primary_terms"] and st["primary_terms"][node] < st["term"]:
        return _reject(st, node, "stale", "stale_term")
    if _lease_valid(st, now):
        primary = st["primary"]
        if _reachable(st, primary) < _quorum(st):
            return _reject(st, node, "standby", "minority")
        _append(st, record, now)
        return _view(st, node, "standby", written=True, via=primary)
    return _reject(st, node, "standby", "no_primary")
