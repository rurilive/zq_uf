"""站点地图分片增量合并。

设计要点
--------
- 每个 URL 归属于最后一次携带它的分片；分片按序号依次合并。
- 同 URL 以 lastmod 比较：新者覆盖，相同/更旧跳过，因此重复下载同一分片幂等。
- 刷新某分片时，旧快照中有、新内容中没有的 URL 落墓碑（tombstone），
  保留 ``retention_seconds`` 秒，期内可按时间回滚，过期由 :meth:`Merger.purge` 清理。
- 分片下载失败只记录失败标记，不动索引、不产生误删除，可随后单独重下。
- 每轮合并留存新增/更新/删除/跳过明细，可按轮次或时间回看。
- 每轮可与全量重建结果对比，差异超阈值则阻断（墓碑清理也会被拦下），
  等待 :meth:`Merger.confirm` 人工确认。
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600

_CHANGE_KINDS = ("added", "updated", "deleted", "skipped")


class DiffThresholdExceeded(RuntimeError):
    """增量索引与全量重建结果的差异超过允许阈值。"""

    def __init__(self, report):
        self.report = report
        super().__init__(
            "incremental index diverges from full rebuild: "
            "%(diff_count)d > %(threshold)d" % report
        )


def _norm_lastmod(value):
    """把 lastmod 归一化为可比较的值：数字原样返回，ISO 字符串转 epoch。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return value


def _as_entries(entries):
    """接受 {url: lastmod} 映射或 (url, lastmod) 可迭代对象。"""
    if entries is None:
        return {}
    if hasattr(entries, "items"):
        items = entries.items()
    else:
        items = entries
    return {url: _norm_lastmod(lastmod) for url, lastmod in items}


def _signature(entries):
    digest = hashlib.sha256()
    for url in sorted(entries):
        digest.update(url.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entries[url]).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _shard_key(shard_id):
    # 数字序号优先按数值排，其余按字符串排，保证合并顺序确定。
    return (0, shard_id) if isinstance(shard_id, int) else (1, str(shard_id))


class Merger:
    """分片增量合并器（内存状态，所有时间均可注入以便测试）。"""

    def __init__(self, retention_seconds=DEFAULT_RETENTION_SECONDS):
        self.retention_seconds = retention_seconds
        # url -> {"lastmod", "shard", "first_seen", "updated_at"}
        self.index = {}
        # url -> {"entry", "shard", "deleted_at", "expires_at", "round_id"}
        self.tombstones = {}
        # shard_id -> {"entries", "signature", "last_round"}
        self.shards = {}
        # 每轮一条记录（含统计与逐条变更明细）
        self.history = []
        # shard_id -> {"error", "round_id"}
        self.failed_shards = {}
        # 回滚操作审计日志
        self.rollbacks = []
        self.blocked_round_id = None
        self._active_round = None

    # ------------------------------------------------------------------ #
    # 轮次管理
    # ------------------------------------------------------------------ #
    def _open_round(self, now):
        round_ = {
            "round_id": len(self.history) + 1,
            "started_at": now,
            "finished_at": None,
            "stats": {kind: 0 for kind in _CHANGE_KINDS},
            "changes": {kind: [] for kind in _CHANGE_KINDS},
            "shards": {"succeeded": [], "failed": []},
            "blocked": False,
            "confirmed": False,
            "verified": None,
            # 待落墓碑的 (url, entry, shard_id)：延迟到整轮结束，
            # URL 被同轮其他分片接住时不算删除（分片所有权转移）。
            "pending_tombstones": [],
        }
        self.history.append(round_)
        self._active_round = round_
        return round_

    def _close_round(self, now):
        round_ = self._active_round
        stats = round_["stats"]
        changes = round_["changes"]
        for url, entry, shard_id in round_["pending_tombstones"]:
            live = self.index.get(url)
            if live is not None and live["shard"] == shard_id:
                # 整轮内没有其他分片接住该 URL：确认删除。
                self.tombstones[url] = {
                    "entry": dict(entry),
                    "shard": shard_id,
                    "deleted_at": now,
                    "expires_at": now + self.retention_seconds,
                    "round_id": round_["round_id"],
                    "state": "active",
                }
                del self.index[url]
                stats["deleted"] += 1
                changes["deleted"].append(
                    {
                        "url": url,
                        "lastmod": entry["lastmod"],
                        "shard": shard_id,
                        "deleted_at": now,
                    }
                )
        round_["pending_tombstones"] = []
        round_["finished_at"] = now
        self._active_round = None

    @staticmethod
    def _round_summary(round_):
        return {
            "round_id": round_["round_id"],
            "started_at": round_["started_at"],
            "finished_at": round_["finished_at"],
            "stats": dict(round_["stats"]),
            "shards": {
                "succeeded": list(round_["shards"]["succeeded"]),
                "failed": [dict(item) for item in round_["shards"]["failed"]],
            },
            "blocked": round_["blocked"],
            "confirmed": round_["confirmed"],
            "verified": round_["verified"],
        }

    def _find_round(self, round_id=None, at=None):
        if round_id is not None:
            for round_ in self.history:
                if round_["round_id"] == round_id:
                    return round_
            raise KeyError("unknown round_id: %r" % (round_id,))
        if at is not None:
            candidates = [r for r in self.history if r["started_at"] <= at]
            if not candidates:
                raise KeyError("no round at or before %r" % (at,))
            return candidates[-1]
        if not self.history:
            raise KeyError("no rounds recorded yet")
        return self.history[-1]

    # ------------------------------------------------------------------ #
    # 核心：单分片合并
    # ------------------------------------------------------------------ #
    def merge_shard(self, shard_id, entries, now=None):
        """合并一个分片，返回 ``{added, updated, deleted, skipped, total}``。"""
        now = time.time() if now is None else now
        entries = _as_entries(entries)
        standalone = self._active_round is None
        round_ = self._active_round or self._open_round(now)
        stats = round_["stats"]
        changes = round_["changes"]
        local = {kind: 0 for kind in _CHANGE_KINDS}

        def record(kind, item):
            local[kind] += 1
            stats[kind] += 1
            changes[kind].append(item)

        snapshot = self.shards.get(shard_id)
        signature = _signature(entries)

        if snapshot is not None and snapshot["signature"] == signature:
            # 内容与上次完全相同：幂等短路，不产生任何增删改。
            for url in sorted(entries):
                record(
                    "skipped",
                    {"url": url, "shard": shard_id, "reason": "unchanged"},
                )
        else:
            old_urls = set(snapshot["entries"]) if snapshot else set()
            new_urls = set(entries)

            # 1) 本分片旧有、新内容缺失：先登记，延迟到整轮结束再落墓碑。
            #    若同轮其他分片接住了该 URL（所有权转移），则不算删除。
            for url in sorted(old_urls - new_urls):
                live = self.index.get(url)
                if live is not None and live["shard"] == shard_id:
                    round_["pending_tombstones"].append(
                        (url, dict(live), shard_id)
                    )

            # 2) 新内容逐条按 lastmod 合并。
            for url in sorted(new_urls):
                lastmod = entries[url]
                live = self.index.get(url)
                tomb = self.tombstones.get(url)

                if live is None:
                    if tomb is not None and tomb["state"] == "active":
                        # 墓碑期内 URL 重新出现：复活；墓碑保留为审计记录。
                        tomb["state"] = "replaced"
                        tomb["replaced_at"] = now
                    self.index[url] = {
                        "lastmod": lastmod,
                        "shard": shard_id,
                        "first_seen": now,
                        "updated_at": now,
                    }
                    record(
                        "added",
                        {
                            "url": url,
                            "lastmod": lastmod,
                            "shard": shard_id,
                            "restored": tomb is not None,
                        },
                    )
                elif lastmod > live["lastmod"]:
                    old_lastmod = live["lastmod"]
                    live["lastmod"] = lastmod
                    live["shard"] = shard_id
                    live["updated_at"] = now
                    record(
                        "updated",
                        {
                            "url": url,
                            "lastmod": lastmod,
                            "old_lastmod": old_lastmod,
                            "shard": shard_id,
                        },
                    )
                else:
                    reason = "unchanged" if lastmod == live["lastmod"] else "stale"
                    if reason == "unchanged" and live["shard"] != shard_id:
                        # 同 lastmod 跨分片迁移：接管所有权，内容不算变更。
                        live["shard"] = shard_id
                    record(
                        "skipped",
                        {"url": url, "shard": shard_id, "reason": reason},
                    )

            self.shards[shard_id] = {
                "entries": dict(entries),
                "signature": signature,
                "last_round": round_["round_id"],
            }

        round_["shards"]["succeeded"].append(shard_id)
        self.failed_shards.pop(shard_id, None)
        if standalone:
            self._close_round(now)
        # 删除在整轮结束时统一结算：独立调用读轮次统计，轮次内调用读本地计数。
        deleted = round_["stats"]["deleted"] if standalone else local["deleted"]
        return {
            "shard": shard_id,
            "round_id": round_["round_id"],
            "added": local["added"],
            "updated": local["updated"],
            "deleted": deleted,
            "skipped": local["skipped"],
            "total": len(self.index),
        }

    # ------------------------------------------------------------------ #
    # 失败隔离与单独重下
    # ------------------------------------------------------------------ #
    def report_failure(self, shard_id, error, now=None):
        """登记一个下载失败的分片：保留旧数据，不影响其他分片。"""
        now = time.time() if now is None else now
        standalone = self._active_round is None
        round_ = self._active_round or self._open_round(now)
        failure = {"shard": shard_id, "error": repr(error)}
        round_["shards"]["failed"].append(failure)
        self.failed_shards[shard_id] = {
            "error": repr(error),
            "round_id": round_["round_id"],
        }
        if standalone:
            self._close_round(now)
        return dict(failure)

    def retry_shard(self, shard_id, entries, now=None):
        """单独重下并合并一个此前失败（或已存在）的分片，成功后清除失败标记。"""
        return self.merge_shard(shard_id, entries, now=now)

    def merge_round(self, shards, now=None, verify_threshold=None):
        """按分片序号合并一整轮。

        ``shards`` 为 ``{shard_id: entries}`` 映射或 ``(shard_id, result)`` 序列；
        ``result`` 可以是分片内容、返回内容的可调用对象（下载函数）或 Exception
        （下载失败）。单个分片失败/抛错只记录失败，其余分片继续。
        """
        now = time.time() if now is None else now
        if hasattr(shards, "items"):
            shards = list(shards.items())
        round_ = self._open_round(now)
        for shard_id, result in sorted(shards, key=lambda kv: _shard_key(kv[0])):
            if isinstance(result, Exception):
                self.report_failure(shard_id, result, now=now)
                continue
            if callable(result):
                try:
                    result = result()
                except Exception as exc:  # 下载失败隔离
                    self.report_failure(shard_id, exc, now=now)
                    continue
            try:
                self.merge_shard(shard_id, result, now=now)
            except Exception as exc:  # 合并异常同样隔离
                self.report_failure(shard_id, exc, now=now)
        self._close_round(now)

        if verify_threshold is not None:
            try:
                self.verify_rebuild(threshold=verify_threshold)
            except DiffThresholdExceeded:
                round_["blocked"] = True
                round_["verified"] = False
                self.blocked_round_id = round_["round_id"]
                raise
            round_["verified"] = True
        return self._round_summary(round_)

    # ------------------------------------------------------------------ #
    # 墓碑：回滚与清理
    # ------------------------------------------------------------------ #
    def deletions(self):
        """当前仍在保留期内、可回滚的删除记录（state=active）。"""
        return {
            url: dict(tomb)
            for url, tomb in self.tombstones.items()
            if tomb["state"] == "active"
        }

    def rollback(self, since=None, until=None, url=None, now=None):
        """按时间窗口或指定 URL 回滚删除记录，返回恢复/跳过的 URL 列表。

        - ``since``：恢复该时间（含）之后被删除的记录；
        - ``until``：只恢复该时间（含）之前被删除的记录；
        - ``url``：只恢复指定 URL。
        已被更新内容重新占用且 lastmod 不旧于墓碑的 URL 跳过，不回退新数据；
        回滚后墓碑保留为 ``rolled_back`` 审计记录。
        """
        now = time.time() if now is None else now
        restored, skipped = [], []
        for candidate, tomb in sorted(self.tombstones.items()):
            if url is not None and candidate != url:
                continue
            if since is not None and tomb["deleted_at"] < since:
                continue
            if until is not None and tomb["deleted_at"] > until:
                continue
            live = self.index.get(candidate)
            old_entry = tomb["entry"]
            # 已被新内容占用（包括复活后更新的 URL）：不回退新数据。
            if live is not None and live["lastmod"] >= old_entry["lastmod"]:
                skipped.append(candidate)
                continue
            if tomb["state"] != "active":
                # 非活动墓碑且无冲突可恢复，跳过。
                continue
            entry = dict(old_entry)
            entry["updated_at"] = now
            self.index[candidate] = entry
            tomb["state"] = "rolled_back"
            tomb["rolled_back_at"] = now
            restored.append(candidate)

        record = {
            "at": now,
            "since": since,
            "until": until,
            "url": url,
            "restored": sorted(restored),
            "skipped": sorted(skipped),
        }
        self.rollbacks.append(record)
        return record

    def purge(self, now=None):
        """清理超过保留期的墓碑（含复活/回滚后的审计记录）；
        存在未确认的阻断轮次时拒绝清理。"""
        if self.blocked_round_id is not None:
            raise RuntimeError(
                "round %s blocked pending manual confirmation; call confirm() first"
                % self.blocked_round_id
            )
        now = time.time() if now is None else now
        expired = sorted(
            url for url, tomb in self.tombstones.items() if tomb["expires_at"] <= now
        )
        for url in expired:
            del self.tombstones[url]
        return {"purged": len(expired), "urls": expired}

    def confirm(self, round_id=None):
        """人工确认一个被阻断的轮次，解除后续清理限制。"""
        target = self.blocked_round_id if round_id is None else round_id
        round_ = self._find_round(round_id=target)
        round_["confirmed"] = True
        if self.blocked_round_id == round_["round_id"]:
            self.blocked_round_id = None
        return self._round_summary(round_)

    # ------------------------------------------------------------------ #
    # 全量重建对比与差异回看
    # ------------------------------------------------------------------ #
    def full_rebuild(self, shards=None):
        """从各分片最新快照做全量重建，返回 {url: (lastmod, shard)}。

        ``shards`` 可传入外部分片数据（映射或 ``(shard_id, entries)`` 序列），
        缺省使用合并器内已保存的各分片最新快照。
        """
        if shards is None:
            source = {sid: snap["entries"] for sid, snap in self.shards.items()}
        elif hasattr(shards, "items"):
            source = {sid: _as_entries(entries) for sid, entries in shards.items()}
        else:
            source = {sid: _as_entries(entries) for sid, entries in shards}
        expected = {}
        for sid in sorted(source, key=_shard_key):
            for url, lastmod in source[sid].items():
                current = expected.get(url)
                if current is None or lastmod > current[0]:
                    expected[url] = (lastmod, sid)
        return expected

    def verify_rebuild(self, threshold=0, shards=None):
        """对比增量索引与全量重建；差异数超阈值抛 DiffThresholdExceeded。"""
        expected = self.full_rebuild(shards)
        extra, missing, stale = [], [], []
        for url, entry in self.index.items():
            if url not in expected:
                extra.append(url)
            elif expected[url][0] != entry["lastmod"]:
                stale.append(
                    {
                        "url": url,
                        "index_lastmod": entry["lastmod"],
                        "rebuild_lastmod": expected[url][0],
                    }
                )
        for url in expected:
            if url not in self.index:
                missing.append(url)
        report = {
            "round_id": self.history[-1]["round_id"] if self.history else None,
            "extra": sorted(extra),
            "missing": sorted(missing),
            "stale": stale,
            "diff_count": len(extra) + len(missing) + len(stale),
            "threshold": threshold,
        }
        if report["diff_count"] > threshold:
            raise DiffThresholdExceeded(report)
        return report

    def diff(self, round_id=None, at=None):
        """回看某一轮的差异：可按轮次号或时间（取该时间之前最近一轮）。"""
        round_ = self._find_round(round_id=round_id, at=at)
        return {
            "round_id": round_["round_id"],
            "started_at": round_["started_at"],
            "finished_at": round_["finished_at"],
            "stats": dict(round_["stats"]),
            "changes": {kind: list(round_["changes"][kind]) for kind in _CHANGE_KINDS},
            "shards": {
                "succeeded": list(round_["shards"]["succeeded"]),
                "failed": [dict(item) for item in round_["shards"]["failed"]],
            },
            "blocked": round_["blocked"],
            "confirmed": round_["confirmed"],
            "verified": round_["verified"],
        }
