"""快照仓库：按站点保存版本、告警开关、观察期和误报标记。"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timezone

MIN_RETENTION = 2
OBSERVATION_DAYS = 7


def _epoch(value):
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    raise TypeError("unsupported timestamp: {!r}".format(value))


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class SnapshotStore:
    """一个站点一条历史，版本号递增且不因清理而回收。

    - 告警开关只影响 ``alert``，比对结果始终入库。
    - 自动通知在站点观察满 ``observation_days`` 天后才生效。
    - 清理至少保留最近 ``MIN_RETENTION`` 版，被标记的误报样本额外保留。
    """

    def __init__(self, retention=MIN_RETENTION, observation_days=OBSERVATION_DAYS, clock=None):
        self.retention = max(MIN_RETENTION, int(retention))
        self.observation_seconds = float(observation_days) * 86400
        self.clock = clock or time.time
        self._sites = {}

    # ---- 站点状态 -------------------------------------------------
    def now(self):
        return _epoch(self.clock())

    def _state(self, site):
        return self._sites.setdefault(site, {
            "versions": [],
            "next_version": 1,
            "alerts_enabled": True,
            "notifications_requested": False,
            "observation_start": None,
            "false_positives": set(),
            "results": [],
        })

    def sites(self):
        return list(self._sites)

    # ---- 版本 -----------------------------------------------------
    def add(self, site, record):
        state = self._state(site)
        version = state["next_version"]
        state["next_version"] = version + 1
        raw_timestamp = record.get("timestamp")
        timestamp = self.now() if raw_timestamp is None else _epoch(raw_timestamp)
        snapshot = copy.deepcopy(dict(record))
        snapshot.update({
            "site": site,
            "version": version,
            "timestamp": timestamp,
            "created_at": _iso(timestamp),
        })
        state["versions"].append(snapshot)
        if state["observation_start"] is None:
            state["observation_start"] = timestamp
        return copy.deepcopy(snapshot)

    def _versions(self, site):
        state = self._state(site)
        return sorted(state["versions"], key=lambda item: (item["timestamp"], item["version"]))

    def latest(self, site):
        versions = self._versions(site)
        if not versions:
            return None
        return copy.deepcopy(versions[-1])

    def get(self, site, version):
        for item in self._state(site)["versions"]:
            if item["version"] == int(version):
                return copy.deepcopy(item)
        raise KeyError("site {!r} has no version {}".format(site, version))

    def history(self, site, since=None, until=None):
        """按时间取回历史版本；since/until 为时间戳或 datetime（含端点）。"""
        versions = self._versions(site)
        if since is not None:
            versions = [item for item in versions if item["timestamp"] >= _epoch(since)]
        if until is not None:
            versions = [item for item in versions if item["timestamp"] <= _epoch(until)]
        return copy.deepcopy(versions)

    def as_of(self, site, when):
        """取回某个时间点之前的最后一版。"""
        limit = _epoch(when)
        candidates = [item for item in self._versions(site) if item["timestamp"] <= limit]
        if not candidates:
            raise KeyError("site {!r} has no version at or before {}".format(site, limit))
        return copy.deepcopy(candidates[-1])

    def prune(self, site, keep=None, older_than=None):
        """清理历史，返回被删除的版本号。

        最近 ``keep`` 版（默认 retention，且不少于两版）始终保留；
        标记为误报的版本不删除；``older_than`` 为秒数，只清理更早的版本。
        """
        state = self._state(site)
        keep = max(MIN_RETENTION, self.retention if keep is None else int(keep))
        versions = self._versions(site)
        pinned = {item["version"] for item in versions[-keep:]}
        pinned |= set(state["false_positives"])
        cutoff = None if older_than is None else self.now() - float(older_than)
        removed = []
        kept = []
        for item in versions:
            too_old = cutoff is None or item["timestamp"] < cutoff
            if item["version"] not in pinned and too_old:
                removed.append(item["version"])
            else:
                kept.append(item)
        state["versions"] = kept
        return removed

    def prune_all(self, keep=None, older_than=None):
        return {site: self.prune(site, keep=keep, older_than=older_than) for site in self.sites()}

    # ---- 告警与通知 -----------------------------------------------
    def disable_alerts(self, site):
        self._state(site)["alerts_enabled"] = False
        return False

    def enable_alerts(self, site):
        self._state(site)["alerts_enabled"] = True
        return True

    def alerts_enabled(self, site):
        return self._state(site)["alerts_enabled"]

    def set_alerts(self, site, enabled):
        return self.enable_alerts(site) if enabled else self.disable_alerts(site)

    def enable_notifications(self, site):
        """请求开启自动通知；站点未观察满一周时通知仍不会发出。"""
        self._state(site)["notifications_requested"] = True
        return self.notifications_active(site)

    def disable_notifications(self, site):
        self._state(site)["notifications_requested"] = False
        return False

    def notifications_requested(self, site):
        return self._state(site)["notifications_requested"]

    def observation_start(self, site):
        return self._state(site)["observation_start"]

    def observation_elapsed(self, site, at=None):
        start = self._state(site)["observation_start"]
        if start is None:
            return False
        return (_epoch(at) if at is not None else self.now()) - start >= self.observation_seconds

    def notifications_active(self, site):
        return bool(self.notifications_requested(site) and self.observation_elapsed(site)
                    and self.alerts_enabled(site))

    # ---- 误报样本 -------------------------------------------------
    def mark_false_positive(self, site, version):
        """把某一版标记为误报样本；该版本不会被清理，对应告警被撤回。"""
        version = int(version)
        self.get(site, version)  # 不存在则抛 KeyError
        state = self._state(site)
        state["false_positives"].add(version)
        for result in state["results"]:
            target = result.get("new_version", result.get("version"))
            if target == version:
                result["false_positive"] = True
                result["alert"] = False
                result["notify"] = False
        return version

    def is_false_positive(self, site, version):
        return int(version) in self._state(site)["false_positives"]

    def false_positives(self, site):
        return sorted(self._state(site)["false_positives"])

    def false_positive_samples(self, site):
        return [item for item in self.history(site) if self.is_false_positive(site, item["version"])]

    # ---- 比对结果 -------------------------------------------------
    def record_result(self, site, result):
        """告警关闭时也照常保存比对结果。"""
        state = self._state(site)
        stored = copy.deepcopy(dict(result))
        state["results"].append(stored)
        return copy.deepcopy(stored)

    def results(self, site, limit=None):
        items = copy.deepcopy(self._state(site)["results"])
        if limit is not None:
            items = items[-int(limit):]
        return items

    # ---- 便捷入口 -------------------------------------------------
    def snapshot(self, page, site=None):
        from .snap import snapshot as build_snapshot

        return build_snapshot(page, store=self, site=site)

    def diff(self, old, new):
        from .snap import diff as compare

        return compare(old, new, store=self)

    def __repr__(self):
        return "SnapshotStore(sites={!r}, retention={!r})".format(len(self._sites), self.retention)
