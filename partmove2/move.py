"""分区迁移：双写 + 位点追平 + 原子切清单 + 校验 + 回滚。

阶段流转::

    idle -> dual_write -> caught_up -> switched
                  |
                  +-> failed          (校验不通过，停在旧路径)
                  +-> switch_failed   (切换失败，停在旧路径)

    switched / failed / switch_failed -> rolled_back   (旧路径继续服务)

关键保证：
* 双写期间旧路径一直可读可写，新路径同步写入，记录 ``position`` 位点；
* 查询只读清单里当前路径，清单切换是一次赋值，读请求不会看到“半份数据”；
* 追平（``lag == 0``）之前拒绝切换；
* 切换前校验两侧行数与内容，不一致不切换；
* 切换失败停在旧路径，旧路径永不因迁移失败被删除；
* 切换后旧路径保留一个窗口且同步写回，窗口内可安全回滚；
* 回滚把清单切回旧路径，目标分区继续保留一段时间，返回回滚清单。
"""

import datetime
import threading
import time

from .store import Manifest, Store


class MigrationError(RuntimeError):
    """迁移状态或参数错误。"""


class VerificationError(MigrationError):
    """两侧数据不一致。"""


def _iso(ts):
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


class PartitionMigration:
    """单个分区的迁移状态。"""

    def __init__(self, partition, source, target):
        self.partition = partition
        self.source = source
        self.target = target
        self.phase = "new"
        self.dual_write = False
        self.switched = False
        self.caught_up = False
        self.verified = None
        self.position = 0
        self.pending_backfill = []
        self.touched = set()
        # 切换后只写新路径；回滚时这些键要回灌旧路径，否则回滚会丢更新。
        self.post_switch_touched = set()
        self.retained_until = None
        self.source_retained_until = None
        # 旧路径保留期过后不再同步写入，此时旧路径已过期，不能再作为回滚目标。
        self.source_stale = False
        self.switch_result = None
        self.history = []
        self._last_verify = None
        self.retry_of = None
        self.attempt = 1

    def record(self, phase, clock, detail=None):
        self.phase = phase
        entry = {
            "phase": phase,
            "at": _iso(clock()),
            "position": self.position,
            "lag": len(self.pending_backfill),
        }
        if detail is not None:
            entry["detail"] = detail
        self.history.append(entry)
        return entry


class Migration:
    """迁移引擎，持有存储、清单和全部分区迁移状态。"""

    def __init__(self, store=None, manifest=None, clock=None,
                 retention_seconds=3600):
        self.store = store or Store()
        self.manifest = manifest or Manifest()
        self.clock = clock or time.time
        self.retention_seconds = retention_seconds
        self._migrations = {}
        self._lock = threading.RLock()
        # 便于演示/测试注入切换故障；生产默认为 False。
        self.fail_switch = False

    # ---------------------------------------------------------------- 内部
    def _idle_report(self, partition):
        """未开始迁移的分区也给出可观测状态。"""
        path = self._source_path(partition)
        rows = self.store.row_count(path)
        return {
            "partition": partition,
            "source": path,
            "target": None,
            "phase": "idle",
            "stage": "idle",
            "dual_write": False,
            "position": None,
            "lag": 0,
            "pending_rows": 0,
            "caught_up": False,
            "switched": False,
            "verified": None,
            "current_path": self._safe_current(partition),
            "source_rows": rows,
            "target_rows": 0,
            "retained_until": None,
            "retained_until_ts": None,
            "source_retained_until": None,
            "source_retained_until_ts": None,
            "retention_seconds": self.retention_seconds,
            "switch": None,
            "rollback_manifest": [],
            "attempt": 0,
            "retry_of": None,
            "history": [],
        }

    def _require(self, partition):
        try:
            return self._migrations[partition]
        except KeyError:
            raise MigrationError("分区尚未开始迁移: %r" % (partition,))

    def _source_path(self, partition):
        current = self.manifest.resolve_or(partition)
        return partition if current is None else current

    def _register_source(self, partition):
        path = self._source_path(partition)
        self.manifest.register(partition, path)
        self.store.ensure(path)
        return path

    def _retain(self, m):
        """失败/回滚后为目标分区保留一段时间。"""
        m.retained_until = self.clock() + self.retention_seconds
        return m.retained_until

    def _report(self, m):
        return {
            "partition": m.partition,
            "source": m.source,
            "target": m.target,
            "phase": m.phase,
            "stage": m.phase,
            "dual_write": m.dual_write,
            "position": m.position,
            "lag": len(m.pending_backfill),
            "pending_rows": len(m.pending_backfill),
            "caught_up": m.caught_up,
            "switched": m.switched,
            "verified": m.verified,
            "current_path": self._safe_current(m.partition),
            "source_rows": self.store.row_count(m.source),
            "target_rows": self.store.row_count(m.target),
            "retained_until": _iso(m.retained_until),
            "retained_until_ts": m.retained_until,
            "source_retained_until": _iso(m.source_retained_until),
            "source_retained_until_ts": m.source_retained_until,
            "retention_seconds": self.retention_seconds,
            "switch": m.switch_result,
            "rollback_manifest": [self._rollback_entry(m)],
            "attempt": m.attempt,
            "retry_of": m.retry_of,
            "history": list(m.history),
        }

    def _safe_current(self, partition):
        return self.manifest.resolve_or(partition)

    def _rollback_entry(self, m):
        return {
            "partition": m.partition,
            "source": m.source,
            "target": m.target,
            "available": self.store.exists(m.source),
            "current_path": self._safe_current(m.partition),
            "retained_until": _iso(m.retained_until),
        }

    def _refuse_switch(self, m, error):
        result = {
            "partition": m.partition,
            "from": m.source,
            "to": m.target,
            "switched": False,
            "version": self.manifest.version,
            "error": error,
            "phase": m.phase,
            "current_path": self._safe_current(m.partition),
        }
        m.switch_result = result
        return result

    # ---------------------------------------------------------------- 启动
    def start(self, partition, target):
        """开始双写：清点旧路径现有数据作为回填快照，同时打开双写。

        返回迁移报告。重复调用同一分区同一目标返回当前报告；目标不同则报错。
        """
        if not partition or not target:
            raise MigrationError("partition 和 target 不能为空")
        with self._lock:
            existing = self._migrations.get(partition)
            if existing is not None:
                if existing.target != target:
                    raise MigrationError(
                        "分区 %r 已在迁移到 %r，不能再改到 %r"
                        % (partition, existing.target, target)
                    )
                if existing.phase not in ("rolled_back", "failed", "switch_failed"):
                    # 进行中、已追平或已切换：双写仍在，直接返回当前状态。
                    return self._report(existing)
                # 失败/回滚后重试：清空目标分区残留，重新取快照双写。
                m = PartitionMigration(partition, self._source_path(partition), target)
                m.retry_of = existing.phase
                m.attempt = getattr(existing, "attempt", 1) + 1
                self._migrations[partition] = m
                self.store.ensure(target)
                self.store.clear(target)
                snapshot = self.store.rows(m.source)
                m.pending_backfill = sorted(snapshot.items(), key=lambda kv: str(kv[0]))
                m.dual_write = True
                m.caught_up = not m.pending_backfill
                m.phase = "caught_up" if m.caught_up else "dual_write"
                m.record(m.phase, self.clock, {
                    "retry": True,
                    "retry_of": existing.phase,
                    "attempt": m.attempt,
                    "backfill_rows": len(m.pending_backfill),
                })
                return self._report(m)

            source = self._register_source(partition)
            if source == target:
                raise MigrationError("新旧路径不能相同: %r" % (source,))

            m = PartitionMigration(partition, source, target)
            self.store.ensure(target)

            # 快照 + 打开双写在同一把锁内完成，期间的写不会漏。
            snapshot = self.store.rows(source)
            m.pending_backfill = sorted(snapshot.items(), key=lambda kv: str(kv[0]))
            m.dual_write = True
            m.caught_up = not m.pending_backfill
            m.phase = "caught_up" if m.caught_up else "dual_write"
            self._migrations[partition] = m
            m.record(m.phase, self.clock, {
                "backfill_rows": len(m.pending_backfill),
                "source": source,
                "target": target,
            })
            return self._report(m)

    # ---------------------------------------------------------------- 写入
    def _source_rollbackable(self, m):
        """切换后旧路径是否仍与当前数据同步、可作回滚目标。"""
        return (
            m.switched
            and not m.source_stale
            and m.source_retained_until is not None
            and self.clock() < m.source_retained_until
        )

    def _mirror(self, m, key, value, delete=False):
        """把一次写同步到迁移的另一侧，保证任何时刻都能回滚。"""
        if m.dual_write and not m.switched:
            # 迁移中：旧路径是主，新路径跟随写入。
            if delete:
                self.store.delete(m.target, key)
            else:
                self.store.put(m.target, key, value)
            m.touched.add(key)
            m.position += 1
        elif m.switched:
            if not self._source_rollbackable(m):
                # 保留期已过：这次写只落新路径，旧路径开始落后。
                m.source_stale = True
                m.source_retained_until = None
                return
            # 已切换但旧路径仍在保留期：同步写回，回滚不丢新数据。
            if delete:
                self.store.delete(m.source, key)
            else:
                self.store.put(m.source, key, value)
            m.post_switch_touched.add(key)

    def write(self, partition, key, value):
        """写一个键：迁移两边同时写（或切换后写回保留的旧路径）。"""
        with self._lock:
            path = self._register_source(partition)
            self.store.put(path, key, value)
            m = self._migrations.get(partition)
            if m is not None:
                self._mirror(m, key, value)
            return value

    def write_many(self, partition, rows):
        """批量写入，整批在同一把锁内完成。"""
        with self._lock:
            count = 0
            for key, value in dict(rows).items():
                self.write(partition, key, value)
                count += 1
            return count

    def delete(self, partition, key):
        """删除一个键；双写期间两侧同删，切换后保留期内也同步到旧路径。"""
        with self._lock:
            path = self._register_source(partition)
            existed = key in self.store.rows(path)
            self.store.delete(path, key)
            m = self._migrations.get(partition)
            if m is not None:
                self._mirror(m, key, None, delete=True)
            return existed

    # ---------------------------------------------------------------- 读取
    def read(self, partition):
        """按清单当前路径取整分区快照；切换过程中不会读到半份数据。"""
        path = self.manifest.resolve_or(partition, partition)
        return self.store.rows(path)

    def get(self, partition, key, default=None):
        path = self.manifest.resolve_or(partition, partition)
        return self.store.rows(path).get(key, default)

    # ---------------------------------------------------------------- 追平
    def catch_up(self, partition, limit=None):
        """回填快照到新路径，推进位点直到追平。

        limit 为单次最多回填行数（None 表示一次追平），便于分段观察进度。
        """
        with self._lock:
            m = self._require(partition)
            if not m.dual_write or m.switched:
                return self._report(m)

            applied = 0
            while m.pending_backfill and (limit is None or applied < limit):
                key, value = m.pending_backfill.pop(0)
                # 回填期间被双写更新/删除过的键以双写结果为准，避免旧快照覆盖新值。
                if key not in m.touched:
                    self.store.put(m.target, key, value)
                    m.position += 1
                    applied += 1
                # 被 touched 的键已由双写复制，位点已在写入时推进。

            if not m.pending_backfill:
                m.caught_up = True
                m.record("caught_up", self.clock, {"applied": applied})
            else:
                m.record("dual_write", self.clock, {"applied": applied})
            return self._report(m)

    # ---------------------------------------------------------------- 校验
    def verify(self, partition):
        """校验行数与内容，返回两侧行数、摘要和差异。"""
        with self._lock:
            m = self._require(partition)
            diff = self.store.diff(m.source, m.target)
            source_rows = self.store.row_count(m.source)
            target_rows = self.store.row_count(m.target)
            source_digest = self.store.digest(m.source)
            target_digest = self.store.digest(m.target)
            counts_match = source_rows == target_rows
            content_match = source_digest == target_digest
            ok = counts_match and content_match
            pending = len(m.pending_backfill)

            result = {
                "partition": partition,
                "ok": ok,
                "caught_up": pending == 0,
                "lag": pending,
                "position": m.position,
                "source_rows": source_rows,
                "target_rows": target_rows,
                "rows_match": counts_match,
                "content_match": content_match,
                "digest": {
                    "source": source_digest,
                    "target": target_digest,
                },
                "diff": diff,
                "phase": m.phase,
            }
            m._last_verify = result
            m.verified = ok
            # 只记录观察结果，不改阶段：双写在横切期间仍在跑，切换由 cutover 决定。
            return result

    # ---------------------------------------------------------------- 切换
    def cutover(self, partition):
        """追平且校验通过后，原子切换清单到新路径。

        不满足条件时返回 switched=False 且不改清单；切换异常时停在旧路径。
        """
        with self._lock:
            m = self._require(partition)
            if m.switched:
                result = dict(m.switch_result or {})
                result["switched"] = True
                result["already_switched"] = True
                return result

            if not m.dual_write:
                return self._refuse_switch(
                    m, "迁移未处于双写状态，请重新 start 后再切换"
                )

            if m.pending_backfill or not m.caught_up:
                if m.pending_backfill:
                    reason = "尚未追平，拒绝切换（lag=%d）" % len(m.pending_backfill)
                else:
                    reason = "迁移未处于已追平状态，请重新 start 后再切换"
                return self._refuse_switch(m, reason)

            check = self.verify(partition)
            if not check["ok"]:
                # 停在旧路径：清单未动，双写继续，便于排查后再切。
                m.phase = "failed"
                m.record("failed", self.clock, {
                    "reason": "校验未通过，拒绝切换",
                    "source_rows": check["source_rows"],
                    "target_rows": check["target_rows"],
                    "diff": check["diff"],
                })
                result = {
                    "partition": partition,
                    "from": m.source,
                    "to": m.target,
                    "switched": False,
                    "version": self.manifest.version,
                    "error": "校验未通过，拒绝切换",
                    "phase": m.phase,
                    "verify": check,
                }
                m.switch_result = result
                return result

            try:
                if self.fail_switch:
                    raise RuntimeError("注入的切换失败")
                version = self.manifest.switch(partition, m.target)
            except Exception as exc:  # 失败绝不删除旧路径，清单仍在旧路径
                m.phase = "switch_failed"
                m.record("switch_failed", self.clock, {
                    "reason": "切换失败: %s" % (exc,),
                    "current_path": self._safe_current(partition),
                })
                result = {
                    "partition": partition,
                    "from": m.source,
                    "to": m.target,
                    "switched": False,
                    "version": self.manifest.version,
                    "error": "切换失败: %s" % (exc,),
                    "phase": m.phase,
                    "current_path": self._safe_current(partition),
                }
                m.switch_result = result
                return result

            m.switched = True
            m.dual_write = False
            m.phase = "switched"
            # 切换后旧路径也保留一个窗口，便于快速回滚。
            m.source_retained_until = self.clock() + self.retention_seconds
            m.record("switched", self.clock, {"version": version, "path": m.target})
            result = {
                "partition": partition,
                "from": m.source,
                "to": m.target,
                "switched": True,
                "version": version,
                "phase": "switched",
                "source_retained_until": _iso(m.source_retained_until),
                "verify": check,
            }
            m.switch_result = result
            return result

    # 语义别名
    switch = cutover

    def rehearse(self, partition, target):
        """低流量分区演练：双写、回填、校验，并模拟一次切换后立刻还原。

        只验证流程，不改变清单最终指向。
        """
        with self._lock:
            m = self._migrations.get(partition)
            if m is not None and m.switched:
                raise MigrationError(
                    "分区 %r 已切换，演练会改回旧路径，请对新迁移使用 rehearse"
                    % (partition,)
                )
            if m is None:
                self.start(partition, target)
                m = self._migrations[partition]
            elif m.target != target:
                raise MigrationError("分区 %r 已指向 %r" % (partition, m.target))

            self.catch_up(partition)
            check = self.verify(partition)
            checks = {
                "dual_write": m.dual_write,
                "caught_up": m.caught_up,
                "verified": check["ok"],
                "rows_match": check["rows_match"],
                "content_match": check["content_match"],
                "query_interrupted": False,
                "switch_simulated": False,
                "reads_consistent": None,
            }
            simulated = False
            if check["ok"]:
                try:
                    before = self.read(partition)
                    # 只读方不解引擎锁，可在切换前后读到完整快照。
                    self.manifest.switch(partition, m.target)
                    during = self.read(partition)
                    self.manifest.switch(partition, m.source)
                    after = self.read(partition)
                    simulated = True
                    consistent = before == during == after
                    checks["switch_simulated"] = True
                    checks["reads_consistent"] = consistent
                    checks["query_interrupted"] = not consistent
                    checks["transition_versions"] = self.manifest.version
                except Exception as exc:
                    checks["error"] = str(exc)
                    checks["query_interrupted"] = True

            if check["ok"] and simulated:
                m.record("rehearsed", self.clock, checks)
            else:
                m.record("failed", self.clock, checks)
            report = self._report(m)
            report["checks"] = checks
            report["verify"] = check
            report["position"] = m.position
            return report

    # ---------------------------------------------------------------- 编排
    def move(self, partition, target, catch_up_limit=None):
        """完整迁移：启动双写 -> 追平 -> 校验 -> 切换，返回阶段/位点/切换/回滚清单。

        ``catch_up_limit`` 只控制回填批次，move 结束前总会追平再尝试切换。
        """
        started = self.start(partition, target)
        if started["phase"] == "switched":
            return self._with_rollback(started)

        if catch_up_limit is not None:
            # 先按批次回填一段（便于观察位点推进），再一次性追平。
            self.catch_up(partition, limit=catch_up_limit)
        self.catch_up(partition)
        switch = self.cutover(partition)
        m = self._require(partition)
        report = self.progress(partition)
        report["switch"] = switch
        report["verify"] = m._last_verify
        # 切换失败时阶段以回来为准；用户最先看的是这次 move 的结果。
        report["phase"] = switch.get("phase", m.phase)
        report["stage"] = report["phase"]
        report["switched"] = bool(switch.get("switched"))
        report["rollback_manifest"] = [self._rollback_entry(m)]
        return report

    def _with_rollback(self, report):
        m = self._migrations[report["partition"]]
        report = dict(report)
        report["rollback_manifest"] = [self._rollback_entry(m)]
        return report

    # ---------------------------------------------------------------- 回滚
    def rollback(self, partition=None):
        """回滚：清单切回旧路径，目标分区保留一段时间。

        未指定 partition 时回滚全部分区。没有迁移记录时返回空回滚。
        """
        with self._lock:
            if partition is not None:
                m = self._migrations.get(partition)
                targets = [m] if m is not None else []
            else:
                targets = list(self._migrations.values())

            if not targets:
                return {
                    "rolled_back": False,
                    "restored": [],
                    "restored_paths": {},
                    "retained": {},
                    "rollback_manifest": [],
                    "partitions": [],
                    "phase": None,
                    "version": self.manifest.version,
                }

            # 先全部预检，避免回滚到一半才发现某个分区不可回滚（部分回滚最难收拾）。
            for m in targets:
                switched_before = m.switched or \
                    self._safe_current(m.partition) == m.target
                if not switched_before:
                    continue
                if not self.store.exists(m.source):
                    raise MigrationError(
                        "旧路径 %r 已不存在，无法回滚分区 %r"
                        % (m.source, m.partition)
                    )
                if m.source_stale:
                    raise MigrationError(
                        "旧路径 %r 已过保留期并有新写入，数据落后，不能作为回滚目标"
                        % (m.source,)
                    )

            restored = []
            retained = {}
            entries = []
            for m in reversed(targets):
                switched_before = m.switched
                if switched_before or self._safe_current(m.partition) == m.target:
                    self.manifest.switch(m.partition, m.source)
                    restored.append(m.partition)
                m.switched = False
                m.dual_write = False
                m.caught_up = False
                m.source_retained_until = None
                phase = m.phase
                m.record("rolled_back", self.clock, {
                    "from": m.target,
                    "to": m.source,
                    "switched_before": switched_before,
                })
                until = self._retain(m)
                retained[m.partition] = {
                    "target": m.target,
                    "until": _iso(until),
                    "until_ts": until,
                }
                entries.append({
                    "partition": m.partition,
                    "source": m.source,
                    "target": m.target,
                    "switched_before": switched_before,
                    "restored_to": m.source if switched_before else self._safe_current(m.partition),
                    "retained_until": _iso(until),
                    "phase_before": phase,
                    "retained": self.store.exists(m.target),
                })

            return {
                "rolled_back": True,
                "restored": restored,
                "restored_paths": {
                    e["partition"]: e["restored_to"] for e in entries
                },
                "retained": retained,
                "rollback_manifest": entries,
                "partitions": [e["partition"] for e in entries],
                "phase": "rolled_back",
                "version": self.manifest.version,
            }

    # ---------------------------------------------------------------- 观测
    def progress(self, partition=None):
        """查看迁移进度：阶段、位点、lag、两侧行数、保留期。"""
        with self._lock:
            if partition is not None:
                m = self._migrations.get(partition)
                if m is None:
                    return self._idle_report(partition)
                return self._report(m)
            return {
                "partitions": {
                    p: self._report(m) for p, m in self._migrations.items()
                },
                "count": len(self._migrations),
                "version": self.manifest.version,
            }

    # ---------------------------------------------------------------- 清理
    def purge(self, now=None):
        """删除保留期已过的目标分区；旧路径只能经 finalize 显式回收。"""
        with self._lock:
            now = self.clock() if now is None else now
            purged = []
            for m in self._migrations.values():
                if m.retained_until is not None and now >= m.retained_until:
                    if self._safe_current(m.partition) != m.target and \
                            self.store.exists(m.target):
                        self.store.drop(m.target)
                        purged.append({"partition": m.partition, "dropped": m.target})
                    m.retained_until = None
                if m.source_retained_until is not None and \
                        now >= m.source_retained_until:
                    # 旧路径的保留期到了也先不删，只能经 finalize 显式回收。
                    m.source_retained_until = None
            return {
                "purged": purged,
                "count": len(purged),
                "retained": {
                    p: _iso(m.retained_until)
                    for p, m in self._migrations.items()
                    if m.retained_until is not None
                },
                "source_retained": {
                    p: _iso(m.source_retained_until)
                    for p, m in self._migrations.items()
                    if m.source_retained_until is not None
                },
            }

    def finalize(self, partition):
        """切换成功且旧路径保留期满后，显式回收旧路径。"""
        with self._lock:
            m = self._require(partition)
            if not m.switched or self._safe_current(partition) != m.target:
                raise MigrationError("只能回收已切换且当前指向新路径的分区")
            if m.source_retained_until is not None and self.clock() < m.source_retained_until:
                raise MigrationError("旧路径保留期未到，暂不回收")
            existed = self.store.exists(m.source)
            self.store.drop(m.source)
            # 旧路径已回收：停止写回，也不再作为回滚目标（避免把已删路径写回来）。
            m.source_retained_until = None
            m.source_stale = True
            m.record("finalized", self.clock, {"dropped": m.source})
            return {
                "partition": partition,
                "dropped": m.source if existed else None,
                "current_path": self._safe_current(partition),
                "phase": m.phase,
            }

    # ---------------------------------------------------------------- 访问
    def get_manifest(self):
        return self.manifest

    def get_store(self):
        return self.store

    @property
    def version(self):
        return self.manifest.version


class PartitionMover:
    """兼容旧名字的薄包装。"""

    def __init__(self, engine=None):
        self.engine = engine or Migration()

    def __getattr__(self, name):
        return getattr(self.engine, name)


# -------------------------------------------------------------------- 默认引擎
_default_engine = Migration()


def engine():
    return _default_engine


def reset():
    """重置默认引擎（主要给测试用）。"""
    global _default_engine
    _default_engine = Migration()
    return _default_engine


def set_engine(new_engine):
    global _default_engine
    _default_engine = new_engine
    return _default_engine


def get_store():
    return _default_engine.store


def get_manifest():
    return _default_engine.manifest


def start(partition, target):
    return _default_engine.start(partition, target)


def move(partition, target, catch_up_limit=None):
    return _default_engine.move(partition, target, catch_up_limit=catch_up_limit)


migrate = move


def rehearse(partition, target):
    return _default_engine.rehearse(partition, target)


def catch_up(partition, limit=None):
    return _default_engine.catch_up(partition, limit=limit)


def verify(partition):
    return _default_engine.verify(partition)


def cutover(partition):
    return _default_engine.cutover(partition)


switch = cutover


def rollback(partition=None):
    return _default_engine.rollback(partition=partition)


def progress(partition=None):
    return _default_engine.progress(partition=partition)


def write(partition, key, value):
    return _default_engine.write(partition, key, value)


def write_many(partition, rows):
    return _default_engine.write_many(partition, rows)


def delete(partition, key):
    return _default_engine.delete(partition, key)


def read(partition):
    return _default_engine.read(partition)


def get(partition, key, default=None):
    return _default_engine.get(partition, key, default)


def purge(now=None):
    return _default_engine.purge(now=now)


def finalize(partition):
    return _default_engine.finalize(partition)
