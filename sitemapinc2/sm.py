"""站点地图分片增量合并。

按分片合并：同一地址按 lastmod 比较，新的覆盖旧的；上游删掉的地址留墓碑，
保留一段时间后清理，删除记录可按时间回滚；分片按序号幂等合并，重复下载
同一分片不产生重复结果；单个分片失败不影响其他分片；每轮合并后可与全量
重建结果对比，差异超限则暂停等待人工确认。
"""

import threading
import time


class Store:
    """一个站点的合并状态。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._urls = {}    # url -> {"lastmod": ..., "shard": ...}
        self._tombs = {}   # url -> {"lastmod": ..., "shard": ..., "deleted_at": ts}
        self._shards = {}  # shard -> {"seq": int, "urls": set, "status": "ok"|"failed"}
        self._rounds = []  # 每轮差异记录 {"id", "ts", "shard", "seq", added/updated/deleted/skipped}
        self._halted = False

    # -- 合并 ----------------------------------------------------------

    def merge_shard(self, shard, seq, entries, now=None):
        """合并一个分片。entries 为 {url: lastmod}。

        返回 {"shard", "seq", "added", "updated", "deleted", "skipped",
        "total", ...}；重复或过期序号直接跳过，不产生重复结果。
        """
        if now is None:
            now = time.time()
        with self._lock:
            if self._halted:
                raise RuntimeError("与全量重建差异超限，已暂停合并，需人工 confirm() 后继续")
            prev = self._shards.get(shard)
            if prev and prev["status"] == "ok":
                if seq == prev["seq"]:
                    return self._stats(shard, seq, skipped=len(entries), idempotent=True)
                if seq < prev["seq"]:
                    return self._stats(shard, seq, skipped=len(entries), stale=True)
            try:
                added, updated, deleted, skipped = self._plan(shard, entries)
            except Exception:
                self._mark_failed(shard, prev)
                raise
            self._apply(shard, seq, entries, added, updated, deleted, now)
            round_id = self._record(shard, seq, added, updated, deleted, skipped, now)
            stats = self._stats(shard, seq, len(added), len(updated), len(deleted), skipped)
            stats["round"] = round_id
            return stats

    def _plan(self, shard, entries):
        """先算好全部变更再落库，失败时状态不被污染。"""
        if not isinstance(entries, dict):
            raise TypeError("entries 必须是 {url: lastmod} 字典")
        added, updated, skipped = [], [], 0
        for url, lastmod in entries.items():
            cur = self._urls.get(url)
            if cur is None:
                added.append(url)
            elif lastmod > cur["lastmod"]:
                updated.append(url)
            else:
                skipped += 1
        prev = self._shards.get(shard)
        prev_urls = prev["urls"] if prev else set()
        # 只墓碑本分片拥有的地址；已被其他分片用更新 lastmod 抢走的除外
        deleted = [u for u in prev_urls - set(entries)
                   if self._urls.get(u, {}).get("shard") == shard]
        return added, updated, deleted, skipped

    def _apply(self, shard, seq, entries, added, updated, deleted, now):
        for url in added + updated:
            self._tombs.pop(url, None)
            self._urls[url] = {"lastmod": entries[url], "shard": shard}
        for url in deleted:
            rec = self._urls.pop(url)
            self._tombs[url] = {"lastmod": rec["lastmod"], "shard": rec["shard"],
                                "deleted_at": now}
        self._shards[shard] = {"seq": seq, "urls": set(entries), "status": "ok"}

    def _mark_failed(self, shard, prev):
        if prev:
            prev["status"] = "failed"
        else:
            self._shards[shard] = {"seq": None, "urls": set(), "status": "failed"}

    def _record(self, shard, seq, added, updated, deleted, skipped, now):
        self._rounds.append({
            "id": len(self._rounds) + 1, "ts": now, "shard": shard, "seq": seq,
            "added": list(added), "updated": list(updated),
            "deleted": list(deleted), "skipped": skipped,
        })
        return self._rounds[-1]["id"]

    def _stats(self, shard, seq, added=0, updated=0, deleted=0, skipped=0, **extra):
        stats = {"shard": shard, "seq": seq, "added": added, "updated": updated,
                 "deleted": deleted, "skipped": skipped, "total": len(self._urls)}
        stats.update(extra)
        return stats

    # -- 差异回看 --------------------------------------------------------

    def diff(self, round_id=None):
        """查看某一轮的差异；round_id 为 None 时取最近一轮。"""
        with self._lock:
            if not self._rounds:
                return {"added": [], "updated": [], "deleted": []}
            if round_id is None:
                rec = self._rounds[-1]
            else:
                rec = next((r for r in self._rounds if r["id"] == round_id), None)
                if rec is None:
                    raise KeyError("没有这一轮: %r" % (round_id,))
            return {"added": list(rec["added"]), "updated": list(rec["updated"]),
                    "deleted": list(rec["deleted"])}

    def history(self):
        """各轮摘要（id、时间、分片、序号、各项数量），用于按时间回看。"""
        with self._lock:
            return [{"id": r["id"], "ts": r["ts"], "shard": r["shard"], "seq": r["seq"],
                     "added": len(r["added"]), "updated": len(r["updated"]),
                     "deleted": len(r["deleted"]), "skipped": r["skipped"]}
                    for r in self._rounds]

    # -- 全量校验 --------------------------------------------------------

    def verify(self, full_entries, max_diff=0):
        """与全量重建结果对比，差异超过 max_diff 则暂停合并等待人工确认。"""
        with self._lock:
            snap = {u: r["lastmod"] for u, r in self._urls.items()}
            full = dict(full_entries)
            missing = sorted(set(full) - set(snap))
            extra = sorted(set(snap) - set(full))
            changed = sorted(u for u in set(full) & set(snap) if full[u] != snap[u])
            n = len(missing) + len(extra) + len(changed)
            ok = n <= max_diff
            if not ok:
                self._halted = True
            return {"status": "ok" if ok else "needs_review", "diff": n,
                    "missing": missing, "extra": extra, "changed": changed,
                    "halted": self._halted}

    def confirm(self):
        """人工确认差异后恢复合并。"""
        with self._lock:
            self._halted = False
            return {"halted": False}

    # -- 墓碑 ------------------------------------------------------------

    def rollback(self, since):
        """把 deleted_at >= since 的删除记录恢复回索引。"""
        with self._lock:
            restored = []
            for url, tomb in list(self._tombs.items()):
                if tomb["deleted_at"] >= since:
                    del self._tombs[url]
                    if url not in self._urls:
                        self._urls[url] = {"lastmod": tomb["lastmod"], "shard": tomb["shard"]}
                        restored.append(url)
            return {"restored": len(restored), "urls": sorted(restored)}

    def purge(self, before):
        """清理 deleted_at < before 的墓碑（保留期届满后调用）。"""
        with self._lock:
            old = [u for u, t in self._tombs.items() if t["deleted_at"] < before]
            for url in old:
                del self._tombs[url]
            return {"purged": len(old)}

    # -- 状态 ------------------------------------------------------------

    def snapshot(self):
        """当前索引 {url: lastmod}。"""
        with self._lock:
            return {u: r["lastmod"] for u, r in self._urls.items()}

    def reset(self):
        """清空全部状态（测试用）。"""
        with self._lock:
            self._urls.clear()
            self._tombs.clear()
            self._shards.clear()
            self._rounds.clear()
            self._halted = False


_default = Store()


def merge_shard(shard, seq, entries, now=None):
    return _default.merge_shard(shard, seq, entries, now=now)


def diff(round_id=None):
    return _default.diff(round_id)


def history():
    return _default.history()


def verify(full_entries, max_diff=0):
    return _default.verify(full_entries, max_diff=max_diff)


def confirm():
    return _default.confirm()


def rollback(since):
    return _default.rollback(since)


def purge(before):
    return _default.purge(before)


def snapshot():
    return _default.snapshot()


def reset():
    _default.reset()
