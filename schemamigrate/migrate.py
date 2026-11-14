"""双向表结构迁移：每步声明向前/向后变换，逐步校验，失败保留现场。"""

import getpass
import hashlib
import json
import time


def _checksum(rows):
    """稳定校验和：逐行规范化 JSON 后排序，与行顺序无关。"""
    canon = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
    return hashlib.sha256("\n".join(canon).encode("utf-8")).hexdigest()


class MigrationError(Exception):
    """单步迁移校验失败。"""


class Migration:
    """一步迁移。

    version: 应用本步后的版本号。
    forward/backward: rows -> rows 的行级变换；backward 为 None 表示不可回退。
    row_delta: 本步预期的行数变化（升级方向），用于校验前后行数。
    aliases: {新字段: 旧字段}，并存期双写与读时兼容用。
    check: 可选钩子 check(before_rows, after_rows)，抛异常即判定本步失败。
    """

    def __init__(self, version, forward, backward, table="default", note="",
                 row_delta=0, aliases=None, check=None):
        self.version = version
        self.forward = forward
        self.backward = backward
        self.table = table
        self.note = note
        self.row_delta = row_delta
        self.aliases = dict(aliases or {})
        self.check = check


class Migrator:
    """迁移执行器：升级按序、回退逆序，每步前后校验行数和校验和。"""

    def __init__(self, state=None):
        self._state = state if state is not None else {"value": 1}
        self.tables = {}
        self.migrations = {}
        self.aliases = {}
        self.history = []

    @property
    def version(self):
        return self._state["value"]

    def register(self, migration):
        self.migrations[migration.version] = migration
        self.aliases.update(migration.aliases)
        return migration

    # ---- 新旧字段并存期：双写与读时兼容 ----

    def insert(self, table, row):
        """双写：新旧字段互为补充，写一行同时带上两个字段。"""
        row = dict(row)
        for new, old in self.aliases.items():
            if new in row and old not in row:
                row[old] = row[new]
            elif old in row and new not in row:
                row[new] = row[old]
        self.tables.setdefault(table, []).append(row)
        return row

    def read_field(self, row, field, default=None):
        """读时兼容：优先读目标字段，缺失时回退到映射的另一侧。"""
        if field in row:
            return row[field]
        old = self.aliases.get(field)
        if old is not None and old in row:
            return row[old]
        for new, old in self.aliases.items():
            if old == field and new in row:
                return row[new]
        return default

    # ---- 升级 / 回退 ----

    def upgrade(self, target):
        start = self.version
        if target <= start:
            raise ValueError(
                "upgrade target %s must be greater than current %s" % (target, start))
        versions = sorted(v for v in self.migrations if start < v <= target)
        return self._run(target, "upgrade", versions)

    def downgrade(self, target):
        start = self.version
        versions = sorted((v for v in self.migrations if target < v <= start),
                          reverse=True)
        if target >= start or not versions:
            raise NotImplementedError(
                "no downgrade path from %s to %s" % (start, target))
        for v in versions:
            if self.migrations[v].backward is None:
                raise NotImplementedError(
                    "migration %s has no backward transform" % v)
        return self._run(target, "downgrade", versions)

    def _run(self, target, direction, versions):
        start = self.version
        started = time.perf_counter()
        steps = []
        ok = True
        failure = None
        version = start
        for index, v in enumerate(versions):
            step, step_ok, scene = self._apply_step(self.migrations[v], direction)
            steps.append(step)
            if not step_ok:
                ok = False
                failure = {"step": v, "error": step["error"], "scene": scene}
                # 停在该步：版本停在上一个一致状态
                if direction == "upgrade":
                    version = versions[index - 1] if index > 0 else start
                else:
                    version = v
                break
            if direction == "upgrade":
                version = v
            else:
                version = versions[index + 1] if index + 1 < len(versions) else target
        if ok:
            version = target
        result = {
            "ok": ok,
            "direction": direction,
            "from": start,
            "version": version,
            "steps": steps,
            "rollback_point": start if ok else version,
            "operator": getpass.getuser(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        if failure is not None:
            result["failure"] = failure
        self._state["value"] = version
        self.history.append(result)
        return result

    def _apply_step(self, migration, direction):
        table = migration.table
        before = [dict(r) for r in self.tables.get(table, [])]
        checksum_before = _checksum(before)
        transform = migration.forward if direction == "upgrade" else migration.backward
        step = {
            "version": migration.version,
            "direction": direction,
            "table": table,
            "rows_before": len(before),
            "checksum_before": checksum_before,
            "ok": False,
        }
        try:
            after = transform([dict(r) for r in before])
            if after is None:
                after = before
            after = [dict(r) for r in after]
            delta = migration.row_delta if direction == "upgrade" else -migration.row_delta
            expected = len(before) + delta
            if len(after) != expected:
                raise MigrationError(
                    "row count %s -> %s, expected %s"
                    % (len(before), len(after), expected))
            if migration.check is not None:
                migration.check(before, after)
        except Exception as exc:
            # 恢复本步之前的快照；现场（失败时的数据）保留在返回值里
            self.tables[table] = before
            step["rows_after"] = len(before)
            step["checksum_after"] = checksum_before
            step["error"] = "%s: %s" % (type(exc).__name__, exc)
            return step, False, {"table": table, "rows": before}
        self.tables[table] = after
        step["rows_after"] = len(after)
        step["checksum_after"] = _checksum(after)
        step["ok"] = True
        return step, True, None


_version = {"value": 1}
migrator = Migrator(_version)


def current():
    return _version["value"]


def register(migration):
    return migrator.register(migration)


def upgrade(version):
    """按序执行 (current, version] 的迁移步；未注册的版本视为无变更。"""
    return migrator.upgrade(version)


def downgrade(version):
    """逆序执行 (version, current] 的 backward 变换；路径缺失时拒绝。"""
    return migrator.downgrade(version)
