"""站点地图分片增量合并：按 lastmod 比较，删除留墓碑，分片按序号幂等合并。"""

import time
from datetime import datetime

DEFAULT_TOMBSTONE_TTL = 7 * 24 * 3600  # 墓碑默认保留 7 天（秒）

_index = {}        # url -> {"lastmod": float, "deleted": bool, "tombstoned_at": float|None}
_shards = {}       # seq -> {"old": {url: lastmod}, "new": {url: lastmod}}
_shard_urls = set()  # 分片层涉及的 URL，回放时整体重建
_tombstones = {}   # url -> (lastmod, tombstoned_at)，跨回放保留墓碑时间
_last_diff = {"added": [], "updated": [], "deleted": [], "skipped": []}
_tombstone_ttl = DEFAULT_TOMBSTONE_TTL


def configure(tombstone_ttl=None):
    """配置墓碑保留时间（秒）。"""
    global _tombstone_ttl
    if tombstone_ttl is not None:
        _tombstone_ttl = float(tombstone_ttl)


def reset():
    """清空索引、分片记录与差异，恢复默认配置。"""
    global _shard_urls, _last_diff, _tombstone_ttl
    _index.clear()
    _shards.clear()
    _shard_urls = set()
    _tombstones.clear()
    _last_diff = {"added": [], "updated": [], "deleted": [], "skipped": []}
    _tombstone_ttl = DEFAULT_TOMBSTONE_TTL


def snapshot():
    """当前存活 URL -> lastmod（墓碑不在内）。"""
    return {u: e["lastmod"] for u, e in _index.items() if not e["deleted"]}


def diff():
    """上一次 merge 的差异：added/updated/deleted/skipped 四类 URL 列表。"""
    return {k: list(v) for k, v in _last_diff.items()}


def _norm(v):
    """lastmod 归一化为可比较的时间戳：数字直接用，ISO 串解析。"""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).timestamp()


def _purge(now, ttl):
    """清理过期墓碑，返回被清理的 URL 列表。"""
    expired = sorted(
        u for u, e in _index.items()
        if e["deleted"] and e["tombstoned_at"] is not None
        and now - e["tombstoned_at"] >= ttl
    )
    for u in expired:
        del _index[u]
        _tombstones.pop(u, None)
    return expired


def _apply(old, new, now, prev=None, before=None):
    """把一份快照合并进 _index。

    prev：回放时被移除的分片 URL 的原 lastmod（用于补墓碑）。
    before：回放前这些 URL 的索引条目，用于把重建结果对照旧状态分类。
    """
    d = {"added": [], "updated": [], "deleted": [], "skipped": []}
    for url in old:
        if url in new:
            continue
        e = _index.get(url)
        if e is None:
            # 索引里没有：只有回放（prev）或已有墓碑记录时才需要补墓碑
            if not ((prev and url in prev) or url in _tombstones):
                continue
            lm = _norm(old[url])
            t = _tombstones.get(url)
            ts = t[1] if t and t[0] == lm else now
            _index[url] = {"lastmod": lm, "deleted": True, "tombstoned_at": ts}
            _tombstones[url] = (lm, ts)
            d["deleted"].append(url)
        elif e["deleted"]:
            d["skipped"].append(url)  # 已是墓碑，重复删除幂等
        else:
            t = _tombstones.get(url)
            e["deleted"] = True
            e["tombstoned_at"] = t[1] if t and t[0] == e["lastmod"] else now
            _tombstones[url] = (e["lastmod"], e["tombstoned_at"])
            d["deleted"].append(url)
    for url, lm in new.items():
        lm = _norm(lm)
        e = _index.get(url)
        if e is None:
            b = before.get(url) if before else None
            if b is not None and b["deleted"] and lm <= b["lastmod"]:
                # 墓碑未被更晚的 lastmod 复活，保持墓碑
                _index[url] = dict(b)
                d["skipped"].append(url)
                continue
            _index[url] = {"lastmod": lm, "deleted": False, "tombstoned_at": None}
            if b is None or b["deleted"]:
                if b is not None:
                    _tombstones.pop(url, None)
                d["added"].append(url)  # 新增，或更晚 lastmod 复活墓碑
            elif lm > b["lastmod"]:
                d["updated"].append(url)
            else:
                d["skipped"].append(url)
        elif e["deleted"]:
            if lm > e["lastmod"]:
                e.update(lastmod=lm, deleted=False, tombstoned_at=None)
                _tombstones.pop(url, None)
                d["added"].append(url)  # 更晚的 lastmod 复活墓碑
            else:
                d["skipped"].append(url)
        elif lm > e["lastmod"]:
            e["lastmod"] = lm
            d["updated"].append(url)
        else:
            d["skipped"].append(url)  # lastmod 不落后才覆盖，否则跳过
    return d


def _replay(now):
    """移除分片层后按序号重放全部分片，乱序到达也与全量重建一致。"""
    global _shard_urls
    before = {u: dict(_index[u]) for u in _shard_urls if u in _index}
    prev = {u: e["lastmod"] for u, e in before.items() if not e["deleted"]}
    for url in _shard_urls:
        _index.pop(url, None)
    _shard_urls = set()
    d = {"added": [], "updated": [], "deleted": [], "skipped": []}
    for seq in sorted(_shards):
        s = _shards[seq]
        part = _apply(s["old"], s["new"], now, prev=prev, before=before)
        for k in d:
            d[k].extend(part[k])
        _shard_urls |= set(s["old"]) | set(s["new"])
    # 分片不再提及的存活 URL：留墓碑而不是直接消失
    for url, e in before.items():
        if url not in _shard_urls and not e["deleted"]:
            t = _tombstones.get(url)
            ts = t[1] if t and t[0] == e["lastmod"] else now
            _index[url] = {"lastmod": e["lastmod"], "deleted": True,
                           "tombstoned_at": ts}
            _tombstones[url] = (e["lastmod"], ts)
            d["deleted"].append(url)
    return d


def _report(expired):
    live = sum(1 for e in _index.values() if not e["deleted"])
    return {
        "total": live,
        "added": len(_last_diff["added"]),
        "updated": len(_last_diff["updated"]),
        "deleted": len(_last_diff["deleted"]),
        "skipped": len(_last_diff["skipped"]),
        "expired": len(expired),
        "tombstones": sum(1 for e in _index.values() if e["deleted"]),
    }


def merge(old, new, shard_seq=None, now=None, tombstone_ttl=None):
    """增量合并一份快照或一个分片。

    old/new: {url: lastmod}，lastmod 为数字或 ISO 时间串；同 URL 按 lastmod
    比较，新值更晚才覆盖，否则跳过；old 有 new 没有的存活项记删除并留墓碑。
    shard_seq: 分片序号，乱序到达按序号重放，同序号同内容幂等；
    分片模式下 new=None 表示下载失败，保留旧分片与索引。
    返回 {total, added, updated, deleted, skipped, expired, tombstones}。
    """
    global _last_diff
    old = dict(old or {})
    if now is None:
        now = time.time()
    ttl = _tombstone_ttl if tombstone_ttl is None else float(tombstone_ttl)

    if shard_seq is not None:
        if new is None:  # 下载失败：保留旧索引，可稍后单独重下
            kept = _shards.get(shard_seq)
            _last_diff = {"added": [], "updated": [], "deleted": [],
                          "skipped": sorted(kept["new"]) if kept else []}
            return _report([])
        new = dict(new)
        prev = _shards.get(shard_seq)
        if prev and prev["old"] == old and prev["new"] == new:
            # 重复分片幂等：不动索引，全部记跳过
            _last_diff = {"added": [], "updated": [], "deleted": [],
                          "skipped": sorted(new)}
            return _report([])
        _shards[shard_seq] = {"old": old, "new": new}
        d = _replay(now)
    else:
        d = _apply(old, dict(new or {}), now)

    expired = _purge(now, ttl)
    _last_diff = {k: sorted(v) for k, v in d.items()}
    return _report(expired)
