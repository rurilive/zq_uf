"""规则路由与迁移。

分区按规则落到存储前缀：规则按数据集、分区时间（dt=）和标签匹配，
多条命中时按优先级解决（同优先级按规则名字典序，结果稳定）。
迁移先生成映射并双读，切换后停旧路径，可回滚。
规则表和迁移记录落盘在 state_dir（默认 .partitionmap/，
可用环境变量 PARTITIONMAP_STATE_DIR 或 configure(state_dir=...) 覆盖）。
"""

import json
import os
import time
import uuid
from fnmatch import fnmatch

_DEFAULT_STATE_DIR = ".partitionmap"


class RouteResult(str):
    """路由结果：字符串即落盘路径，附带命中规则、冲突与双读信息。"""

    def __new__(cls, path, rule=None, conflict=None, old_path=None, dual_read=False):
        obj = str.__new__(cls, path)
        obj.path = path
        obj.rule = rule  # 命中的规则 dict，无命中为 None
        obj.conflict = conflict  # 冲突信息 dict，无冲突为 None
        obj.old_path = old_path  # 双读期间的旧路径，否则为 None
        obj.dual_read = dual_read
        return obj


def _parse_kv(partition):
    """把 'dt=2026-01-01/hr=01' 或 'dt=1&tag=a' 解析成键值对。"""
    kv = {}
    for part in partition.replace("&", "/").split("/"):
        if "=" in part:
            key, value = part.split("=", 1)
            kv[key] = value
    return kv


def _rule_matches(rule, dataset, kv, tags):
    ds = rule.get("dataset", "*")
    if ds != "*" and not fnmatch(dataset, ds):
        return False
    dt = kv.get("dt")
    start = rule.get("start")
    end = rule.get("end")
    if start is not None and (dt is None or dt < start):
        return False
    if end is not None and (dt is None or dt > end):
        return False
    have = dict(kv)
    have.update(tags or {})
    for key, value in rule.get("tags", {}).items():
        if have.get(key) != value:
            return False
    return True


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


class PartitionMap:
    """规则路由 + 迁移状态机，状态落盘在 state_dir。"""

    def __init__(self, state_dir=None):
        self.state_dir = state_dir or os.environ.get(
            "PARTITIONMAP_STATE_DIR", _DEFAULT_STATE_DIR
        )

    # -- 落盘 ---------------------------------------------------------

    @property
    def _rules_path(self):
        return os.path.join(self.state_dir, "rules.json")

    @property
    def _migrations_path(self):
        return os.path.join(self.state_dir, "migrations.json")

    def _save(self, path, obj):
        os.makedirs(self.state_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)

    # -- 规则表 ---------------------------------------------------------

    def rules(self):
        return _load(self._rules_path, [])

    def add_rule(self, rule):
        """登记规则。字段：name, dataset, start, end, tags, target, priority。"""
        rule = dict(rule)
        if not rule.get("name"):
            raise ValueError("rule needs a name")
        if not rule.get("target"):
            raise ValueError("rule needs a target")
        rule.setdefault("priority", 0)
        rules = [r for r in self.rules() if r.get("name") != rule["name"]]
        rules.append(rule)
        self._save(self._rules_path, rules)
        return rule

    def _match(self, dataset, kv, tags):
        matched = [r for r in self.rules() if _rule_matches(r, dataset, kv, tags)]
        # 优先级高者胜；同优先级按名字字典序，保证路由稳定
        matched.sort(key=lambda r: (-r.get("priority", 0), r.get("name", "")))
        return matched

    # -- 迁移记录 ---------------------------------------------------------

    def migrations(self):
        return _load(self._migrations_path, [])

    def _active_migration(self, dataset):
        for rec in reversed(self.migrations()):
            if rec.get("dataset") == dataset:
                return rec
        return None

    # -- 路由 ---------------------------------------------------------

    def route(self, dataset, partition, tags=None, at=None):
        kv = _parse_kv(partition)
        if at is not None:
            kv.setdefault("dt", at)
        matched = self._match(dataset, kv, tags)
        rule = None
        conflict = None
        if matched:
            rule = matched[0]
            if len(matched) > 1:
                first, second = matched[0], matched[1]
                conflict = {
                    "rules": [first, second],  # 命中的两条规则
                    "winner": first.get("name"),
                    "resolved_by": (
                        "priority"
                        if first.get("priority", 0) != second.get("priority", 0)
                        else "name"
                    ),
                }
            path = rule["target"].format(dataset=dataset, partition=partition)
        else:
            path = "%s/%s" % (dataset, partition)
        old_path = None
        dual_read = False
        rec = self._active_migration(dataset)
        if rec and partition in rec.get("mapping", {}):
            entry = rec["mapping"][partition]
            if rec["status"] == "prepared":
                path = entry["to"]  # 双读：走新路径，旧路径保留兜底
                old_path = entry["from"]
                dual_read = True
            elif rec["status"] == "switched":
                path = entry["to"]  # 切换后停旧路径
            elif rec["status"] == "rolled_back":
                path = entry["from"]
        return RouteResult(
            path, rule=rule, conflict=conflict, old_path=old_path, dual_read=dual_read
        )

    # -- 迁移 ---------------------------------------------------------

    def migrate(self, plan):
        """执行迁移计划。

        plan["action"]：
          prepare（默认）生成映射并进入双读，moved=0；
          switch  停旧路径，moved=映射内分区数；
          rollback 回滚到旧路径，moved=0。
        """
        plan = dict(plan or {})
        action = plan.get("action", "prepare")
        records = self.migrations()
        if action == "prepare":
            rec = self._prepare(plan)
            records.append(rec)
            self._save(self._migrations_path, records)
            return {
                "id": rec["id"],
                "status": "prepared",
                "moved": 0,
                "mapping": rec["mapping"],
                "dual_read": True,
            }
        if action not in ("switch", "rollback"):
            raise ValueError("unknown migrate action: %r" % action)
        rec = self._find_record(records, plan)
        if rec is None:
            return {"status": "noop", "moved": 0, "mapping": {}}
        if action == "switch":
            new = dict(rec, action="switch", status="switched",
                       moved=len(rec["mapping"]), ts=time.time())
        else:
            new = dict(rec, action="rollback", status="rolled_back",
                       moved=0, ts=time.time())
        records.append(new)
        self._save(self._migrations_path, records)
        return {
            "id": new["id"],
            "status": new["status"],
            "moved": new["moved"],
            "mapping": new["mapping"],
            "dual_read": False,
        }

    def _prepare(self, plan):
        dataset = plan.get("dataset", "*")
        to_prefix = plan.get("to")
        mapping = {}
        for part in plan.get("partitions", []):
            entry = {"from": "%s/%s" % (dataset, part)}
            entry["to"] = (
                "%s/%s/%s" % (to_prefix, dataset, part) if to_prefix else entry["from"]
            )
            mapping[part] = entry
        for part, value in plan.get("mapping", {}).items():
            if isinstance(value, str):
                mapping[part] = {
                    "from": "%s/%s" % (dataset, part),
                    "to": value,
                }
            else:
                mapping[part] = dict(value)
        return {
            "id": uuid.uuid4().hex[:12],
            "action": "prepare",
            "dataset": dataset,
            "mapping": mapping,
            "status": "prepared",
            "moved": 0,
            "ts": time.time(),
        }

    def _find_record(self, records, plan):
        mid = plan.get("id")
        dataset = plan.get("dataset")
        for rec in reversed(records):
            if mid and rec.get("id") != mid:
                continue
            if not mid and dataset and rec.get("dataset") != dataset:
                continue
            if rec.get("status") in ("prepared", "switched"):
                return rec
        return None


# -- 模块级默认实例 ---------------------------------------------------------

_default = None


def configure(state_dir=None):
    """重置默认实例，可指定状态目录（规则表与迁移记录落盘位置）。"""
    global _default
    _default = PartitionMap(state_dir)
    return _default


def _map():
    global _default
    if _default is None:
        _default = PartitionMap()
    return _default


def route(dataset, partition, tags=None, at=None):
    """路由一个分区，返回 RouteResult（字符串即落盘路径）。"""
    return _map().route(dataset, partition, tags=tags, at=at)


def migrate(plan):
    """执行迁移计划，返回迁移结果 dict。"""
    return _map().migrate(plan)


def add_rule(rule):
    return _map().add_rule(rule)


def rules():
    return _map().rules()


def migrations():
    return _map().migrations()
