"""站点规则注册表：按站点管理规则，支持版本快照与回滚。"""

import copy
import threading

from .classifier import CLASSES
from .rules import InvalidRule, Rule, rule_from_dict


class Registry:
    """线程安全的站点规则注册表。

    每次 ``add_rule`` / ``remove_rule`` / ``configure_site`` 都会为对应站点
    生成一个不可变修订版本（revision），``rollback`` 可恢复到任意历史版本，
    回滚本身也会留下新版本，历史不会丢失。
    """

    def __init__(self):
        self._lock = threading.RLock()
        # site -> {"rules": [Rule...], "extras": {cls: [names...]}}
        self._sites = {}
        # site -> [ {"revision": int, "rules": [dict...], "extras": {...}} ]
        self._revisions = {}
        self._seq = {}
        self._records = []

    # ------------------------------------------------------------------ 配置

    def configure_site(self, site, tracking=None, pagination=None):
        """扩展站点自己的追踪/分页参数表。"""
        with self._lock:
            config = self._ensure_site(site)
            for cls, extra in (
                ("tracking", tracking),
                ("pagination", pagination),
            ):
                if extra is None:
                    continue
                if not isinstance(extra, (list, tuple)):
                    raise InvalidRule(
                        "站点 %r 的 %s 扩展参数必须是列表" % (site, cls)
                    )
                merged = list(dict.fromkeys(config["extras"][cls] + list(extra)))
                config["extras"][cls] = merged
            self._snapshot(site)

    def _ensure_site(self, site):
        """返回站点配置；首次接入时登记并落一个空配置快照（rev 1）。"""
        if site not in self._sites:
            self._sites[site] = {
                "rules": [],
                "extras": {cls: [] for cls in CLASSES},
            }
            self._snapshot(site)
        return self._sites[site]

    def add_rule(self, site, rule):
        """向站点注册规则；字典声明会先做合法性校验。"""
        with self._lock:
            if isinstance(rule, dict):
                rule = rule_from_dict(rule)
            elif not isinstance(rule, Rule):
                raise InvalidRule(
                    "add_rule 需要 Rule 对象或规则字典，收到 %r"
                    % type(rule).__name__
                )
            config = self._ensure_site(site)
            if any(existing.id == rule.id for existing in config["rules"]):
                raise InvalidRule(
                    "站点 %r 已存在规则 id=%r" % (site, rule.id)
                )
            config["rules"].append(rule)
            self._snapshot(site)
            return rule

    def remove_rule(self, site, rule_id):
        with self._lock:
            config = self._sites.get(site)
            if config is None or not any(
                rule.id == rule_id for rule in config["rules"]
            ):
                raise InvalidRule(
                    "站点 %r 不存在规则 id=%r" % (site, rule_id)
                )
            config["rules"] = [
                rule for rule in config["rules"] if rule.id != rule_id
            ]
            self._snapshot(site)

    def get_rules(self, site):
        """返回站点当前生效的规则，按优先级（降序）、注册顺序（升序）排列。"""
        with self._lock:
            config = self._sites.get(site)
            if config is None:
                return []
            ordered = sorted(
                config["rules"],
                key=lambda rule: (-rule.priority, self._order(site, rule)),
            )
            return list(ordered)

    def _order(self, site, rule):
        # 注册顺序即当前列表中的位置
        return self._sites[site]["rules"].index(rule)

    def extras(self, site):
        config = self._sites.get(site)
        if config is None:
            return {"tracking": [], "pagination": []}
        return {
            "tracking": list(config["extras"]["tracking"]),
            "pagination": list(config["extras"]["pagination"]),
        }

    def sites(self):
        with self._lock:
            return sorted(self._sites)

    # ------------------------------------------------------------ 版本/回滚

    def _snapshot(self, site):
        config = self._sites[site]
        rev = self._seq.get(site, 0) + 1
        self._seq[site] = rev
        snapshot = {
            "revision": rev,
            "rules": [rule.to_dict() for rule in config["rules"]],
            "extras": copy.deepcopy(config["extras"]),
        }
        self._revisions.setdefault(site, []).append(snapshot)
        return rev

    def revisions(self, site):
        """返回站点的修订版本号列表（升序）。"""
        with self._lock:
            return [item["revision"] for item in self._revisions.get(site, [])]

    def current_revision(self, site):
        revs = self._revisions.get(site)
        if not revs:
            return 0
        return revs[-1]["revision"]

    def rollback(self, site, revision):
        """把站点规则恢复到指定修订版本；回滚动作本身生成一个新版本。

        revision=0 表示恢复到“无任何配置”的初始状态。
        """
        with self._lock:
            if revision == 0:
                target = {
                    "rules": [],
                    "extras": {cls: [] for cls in CLASSES},
                }
            else:
                history = self._revisions.get(site, [])
                match = next(
                    (item for item in history if item["revision"] == revision),
                    None,
                )
                if match is None:
                    raise InvalidRule(
                        "站点 %r 不存在修订版本 %s" % (site, revision)
                    )
                target = {
                    "rules": [rule_from_dict(d) for d in match["rules"]],
                    "extras": copy.deepcopy(match["extras"]),
                }
            self._sites[site] = target
            new_rev = self._snapshot(site)
            return new_rev

    # ---------------------------------------------------------------- 记录

    def add_record(self, record):
        with self._lock:
            self._records.append(record)
            return len(self._records)

    def history(self, site=None, limit=None):
        """查询重写记录，可按站点过滤；limit 仅返回最近 N 条。"""
        with self._lock:
            items = [
                copy.deepcopy(record)
                for record in self._records
                if site is None or record["site"] == site
            ]
        if limit is not None:
            return items[-limit:]
        return items

    def clear_history(self):
        with self._lock:
            self._records.clear()
