"""采集任务准入与名单热更新（纯 Python 标准库）。

公开接口保持 ``admit(task)`` 与 ``reload()``：

* ``admit`` 按任务来源、目标站点和名单校验，重复任务按指纹拒绝；
  拒绝会写审计并返回原因、当前名单版本。
* ``reload`` 读取名单文件，全部解析/校验成功后才原子替换当前快照；
  任何失败都保留旧名单。

名单文件为 JSON，带 ``version``（版本号）与 ``effective_at``（生效时间）。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

REASON_INVALID_TASK = "invalid_task"
REASON_SOURCE_DENIED = "source_denied"
REASON_SOURCE_NOT_ALLOWED = "source_not_allowed"
REASON_SITE_DENIED = "site_denied"
REASON_SITE_NOT_ALLOWED = "site_not_allowed"
REASON_DUPLICATE = "duplicate"

_RELOAD_NO_SOURCE = "no_source"
_RELOAD_INVALID = "invalid_list"
_RELOAD_UNCHANGED = "unchanged"
_RELOAD_VERSION_CONFLICT = "version_conflict"
_RELOAD_NOT_EFFECTIVE = "not_effective_yet"
_RELOAD_ACTIVATED = "activated"

_RELOAD_HISTORY_LIMIT = 100


class ListError(ValueError):
    """名单文件内容不合法。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return _aware(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize(value: Any) -> str:
    return _text(value).lower()


def _matches(value: str, patterns: frozenset[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    return _aware(datetime.fromisoformat(text))


def _fingerprint(task: Mapping[str, Any], source: str, site: str, url: str) -> str:
    """任务指纹：显式 fingerprint 优先；否则来源+站点+URL，无 URL 时用来源+站点+id。"""
    explicit = _text(task.get("fingerprint"))
    if explicit:
        return explicit
    if url:
        identity = {"source": source, "site": site, "url": url}
    else:
        identity = {
            "source": source,
            "site": site,
            "id": _text(task.get("id")),
        }
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_patterns(block: Any, key: str, label: str) -> frozenset[str]:
    if block is None:
        return frozenset()
    if not isinstance(block, Mapping):
        raise ListError(f"{label} 所在块必须是对象")
    values = block.get(key, [])
    if values is None:
        return frozenset()
    if not isinstance(values, list):
        raise ListError(f"{label} 必须是数组")
    patterns = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ListError(f"{label} 的每一项都必须是非空字符串")
        patterns.append(value.strip().lower())
    return frozenset(patterns)


@dataclass(frozen=True)
class _Snapshot:
    version: Optional[str] = None
    effective_at: Optional[str] = None
    effective_ts: Optional[datetime] = None
    source_allow: frozenset[str] = frozenset()
    source_deny: frozenset[str] = frozenset()
    site_allow: frozenset[str] = frozenset()
    site_deny: frozenset[str] = frozenset()
    digest: str = ""
    loaded_at: Optional[str] = None
    path: Optional[str] = None


_EMPTY = _Snapshot()


def _load_snapshot(path: str | os.PathLike[str]) -> _Snapshot:
    with open(os.fspath(path), "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise ListError("名单根节点必须是对象")

    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ListError("名单缺少非空 version")

    effective_at = data.get("effective_at")
    if not isinstance(effective_at, str) or not effective_at.strip():
        raise ListError("名单缺少非空 effective_at")
    try:
        effective_ts = _parse_time(effective_at)
    except ValueError as exc:
        raise ListError(f"effective_at 不是合法 ISO 8601 时间: {exc}") from exc

    source_allow = _load_patterns(data.get("sources"), "allow", "sources.allow")
    source_deny = _load_patterns(data.get("sources"), "deny", "sources.deny")
    site_allow = _load_patterns(data.get("sites"), "allow", "sites.allow")
    site_deny = _load_patterns(data.get("sites"), "deny", "sites.deny")

    effective_text = _iso(effective_ts)
    canonical = json.dumps(
        {
            "version": version.strip(),
            "effective_at": effective_text,
            "sources": {
                "allow": sorted(source_allow),
                "deny": sorted(source_deny),
            },
            "sites": {
                "allow": sorted(site_allow),
                "deny": sorted(site_deny),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return _Snapshot(
        version=version.strip(),
        effective_at=effective_text,
        effective_ts=effective_ts,
        source_allow=source_allow,
        source_deny=source_deny,
        site_allow=site_allow,
        site_deny=site_deny,
        digest=digest,
    )


class Guard:
    """独立的准入器：持有名单快照、指纹表和审计日志。"""

    def __init__(
        self,
        path: Optional[str | os.PathLike[str]] = None,
        *,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._now = now or _utcnow
        self._path = os.fspath(path) if path is not None else None
        self._snapshot = _EMPTY
        self._pending: Optional[_Snapshot] = None
        self._seen: dict[str, str] = {}
        self._audit: list[dict[str, Any]] = []
        self._reloads: list[dict[str, Any]] = []
        self._sites: dict[str, dict[str, Any]] = {}
        self._admitted = 0
        self._rejected = 0

    # -- 准入 ---------------------------------------------------------

    def admit(self, task: Any) -> dict[str, Any]:
        now = _aware(self._now())
        with self._lock:
            snapshot = self._snapshot
            if not isinstance(task, Mapping):
                entry = self._audit_entry(
                    now,
                    task_id="",
                    source="",
                    site="",
                    fingerprint="",
                    reason=REASON_INVALID_TASK,
                    message="任务必须是映射对象",
                    snapshot=snapshot,
                )
                self._record("", admitted=False, reason=REASON_INVALID_TASK)
                return self._result(
                    task_id="",
                    source="",
                    site="",
                    fingerprint="",
                    admitted=False,
                    reason=REASON_INVALID_TASK,
                    message="任务必须是映射对象",
                    snapshot=snapshot,
                    audit=[entry],
                )

            task_id = _text(task.get("id"))
            source = _normalize(task.get("source"))
            site = _normalize(task.get("site"))
            url = _text(task.get("url"))
            fingerprint = _fingerprint(task, source, site, url)

            reason = ""
            message = ""
            duplicate_of: Optional[str] = None
            if _matches(source, snapshot.source_deny):
                reason = REASON_SOURCE_DENIED
                message = f"来源 {source!r} 命中来源黑名单"
            elif snapshot.source_allow and not _matches(source, snapshot.source_allow):
                reason = REASON_SOURCE_NOT_ALLOWED
                message = f"来源 {source!r} 不在来源白名单"
            elif _matches(site, snapshot.site_deny):
                reason = REASON_SITE_DENIED
                message = f"站点 {site!r} 命中站点黑名单"
            elif snapshot.site_allow and not _matches(site, snapshot.site_allow):
                reason = REASON_SITE_NOT_ALLOWED
                message = f"站点 {site!r} 不在站点白名单"
            elif fingerprint in self._seen:
                reason = REASON_DUPLICATE
                duplicate_of = self._seen[fingerprint]
                message = (
                    f"指纹 {fingerprint[:12]} 已准入（任务 {duplicate_of!r}）"
                )

            if reason:
                entry = self._audit_entry(
                    now,
                    task_id=task_id,
                    source=source,
                    site=site,
                    fingerprint=fingerprint,
                    reason=reason,
                    message=message,
                    snapshot=snapshot,
                    duplicate_of=duplicate_of,
                )
                self._record(site, admitted=False, reason=reason)
                return self._result(
                    task_id=task_id,
                    source=source,
                    site=site,
                    fingerprint=fingerprint,
                    admitted=False,
                    reason=reason,
                    message=message,
                    snapshot=snapshot,
                    audit=[entry],
                )

            self._seen[fingerprint] = task_id
            self._record(site, admitted=True, reason="")
            return self._result(
                task_id=task_id,
                source=source,
                site=site,
                fingerprint=fingerprint,
                admitted=True,
                reason="",
                message="",
                snapshot=snapshot,
                audit=[],
            )

    # -- 名单热更新 ---------------------------------------------------

    def reload(self, path: Optional[str | os.PathLike[str]] = None) -> dict[str, Any]:
        # 整个流程持锁：并发 reload 不会交错，快照替换始终是原子的。
        with self._lock:
            target = os.fspath(path) if path is not None else self._path
            current = self._snapshot

            if not target:
                return self._finish_reload(
                    {
                        "reloaded": False,
                        "version": current.version,
                        "effective_at": current.effective_at,
                        "kept": True,
                        "reason": _RELOAD_NO_SOURCE,
                        "error": "未配置名单文件，可用 configure(path) 或 reload(path) 指定",
                        "path": None,
                    }
                )

            now = _aware(self._now())

            # 已暂存但未到生效时间的名单：到点即原子生效，无需再读文件。
            pending = self._pending
            if (
                pending is not None
                and pending.path == target
                and pending.effective_ts is not None
            ):
                if pending.effective_ts <= now:
                    previous = current.version
                    self._snapshot = replace(pending, loaded_at=_iso(now))
                    self._path = pending.path
                    self._pending = None
                    return self._finish_reload(
                        {
                            "reloaded": True,
                            "version": self._snapshot.version,
                            "effective_at": self._snapshot.effective_at,
                            "previous_version": previous,
                            "kept": False,
                            "reason": _RELOAD_ACTIVATED,
                            "path": self._path,
                        }
                    )

            try:
                candidate = _load_snapshot(target)
            except (OSError, ValueError) as exc:
                return self._finish_reload(
                    {
                        "reloaded": False,
                        "version": current.version,
                        "effective_at": current.effective_at,
                        "kept": True,
                        "reason": _RELOAD_INVALID,
                        "error": str(exc),
                        "path": target,
                    }
                )

            if candidate.version == current.version:
                if candidate.digest == current.digest:
                    self._path = target
                    self._pending = None
                    result = {
                        "reloaded": False,
                        "version": current.version,
                        "effective_at": current.effective_at,
                        "previous_version": current.version,
                        "kept": True,
                        "reason": _RELOAD_UNCHANGED,
                        "path": target,
                    }
                else:
                    result = {
                        "reloaded": False,
                        "version": current.version,
                        "effective_at": current.effective_at,
                        "previous_version": current.version,
                        "kept": True,
                        "reason": _RELOAD_VERSION_CONFLICT,
                        "error": (
                            f"版本号 {candidate.version!r} 未变化但内容不同，"
                            "请为新名单分配新版本号"
                        ),
                        "path": target,
                    }
            elif candidate.effective_ts is not None and candidate.effective_ts > now:
                # 校验通过但尚未到生效时间：暂存，等下一次 reload 到点生效。
                self._path = target
                self._pending = replace(candidate, path=target)
                result = {
                    "reloaded": False,
                    "version": current.version,
                    "effective_at": current.effective_at,
                    "previous_version": current.version,
                    "kept": True,
                    "reason": _RELOAD_NOT_EFFECTIVE,
                    "pending_version": candidate.version,
                    "pending_effective_at": candidate.effective_at,
                    "path": target,
                }
            else:
                previous = current.version
                self._snapshot = _Snapshot(
                    version=candidate.version,
                    effective_at=candidate.effective_at,
                    effective_ts=candidate.effective_ts,
                    source_allow=candidate.source_allow,
                    source_deny=candidate.source_deny,
                    site_allow=candidate.site_allow,
                    site_deny=candidate.site_deny,
                    digest=candidate.digest,
                    loaded_at=_iso(now),
                    path=target,
                )
                self._path = target
                self._pending = None
                result = {
                    "reloaded": True,
                    "version": self._snapshot.version,
                    "effective_at": self._snapshot.effective_at,
                    "previous_version": previous,
                    "kept": False,
                    "reason": "",
                    "path": target,
                }
            return self._finish_reload(result)

    def _finish_reload(self, result: dict[str, Any]) -> dict[str, Any]:
        """记录一次热更新结果（调用方须持锁）。"""
        self._reloads.append(dict(result))
        if len(self._reloads) > _RELOAD_HISTORY_LIMIT:
            del self._reloads[: len(self._reloads) - _RELOAD_HISTORY_LIMIT]
        return result

    def reload_history(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._reloads]

    # -- 输出 / 审计 / 汇总 -------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._snapshot
            return {
                "list_version": snapshot.version,
                "list_effective_at": snapshot.effective_at,
                "list_path": self._path,
                "list_loaded_at": snapshot.loaded_at,
                "source_allow": sorted(snapshot.source_allow),
                "source_deny": sorted(snapshot.source_deny),
                "site_allow": sorted(snapshot.site_allow),
                "site_deny": sorted(snapshot.site_deny),
                "admitted": self._admitted,
                "rejected": self._rejected,
                "fingerprints": len(self._seen),
                "audit_size": len(self._audit),
            }

    def audit_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._audit]

    def export_rejections(
        self, path: Optional[str | os.PathLike[str]] = None
    ) -> dict[str, Any]:
        now = _aware(self._now())
        with self._lock:
            records = [
                dict(entry)
                for entry in self._audit
                if entry.get("event") == "reject"
            ]
            version = self._snapshot.version
        payload = {
            "exported_at": _iso(now),
            "list_version": version,
            "count": len(records),
            "rejections": records,
        }
        if path is not None:
            target = os.fspath(path)
            with open(target, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            payload["path"] = target
        return payload

    def summary(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._snapshot
            sites = {
                site: {
                    "admitted": bucket["admitted"],
                    "rejected": bucket["rejected"],
                    "reasons": dict(bucket["reasons"]),
                }
                for site, bucket in self._sites.items()
            }
            return {
                "list_version": snapshot.version,
                "list_effective_at": snapshot.effective_at,
                "sites": sites,
                "totals": {
                    "admitted": self._admitted,
                    "rejected": self._rejected,
                    "tasks": self._admitted + self._rejected,
                },
            }

    # -- 内部 ---------------------------------------------------------

    def _record(self, site: str, *, admitted: bool, reason: str) -> None:
        bucket = self._sites.setdefault(
            site, {"admitted": 0, "rejected": 0, "reasons": {}}
        )
        if admitted:
            bucket["admitted"] += 1
            self._admitted += 1
        else:
            bucket["rejected"] += 1
            bucket["reasons"][reason] = bucket["reasons"].get(reason, 0) + 1
            self._rejected += 1

    def _audit_entry(
        self,
        now: datetime,
        *,
        task_id: str,
        source: str,
        site: str,
        fingerprint: str,
        reason: str,
        message: str,
        snapshot: _Snapshot,
        duplicate_of: Optional[str] = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "at": _iso(now),
            "event": "reject",
            "task": task_id,
            "source": source,
            "site": site,
            "fingerprint": fingerprint,
            "reason": reason,
            "reason_text": message,
            "list_version": snapshot.version,
            "list_effective_at": snapshot.effective_at,
        }
        if duplicate_of is not None:
            entry["duplicate_of"] = duplicate_of
        self._audit.append(entry)
        return dict(entry)

    def _result(
        self,
        *,
        task_id: str,
        source: str,
        site: str,
        fingerprint: str,
        admitted: bool,
        reason: str,
        message: str,
        snapshot: _Snapshot,
        audit: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "task": task_id,
            "admitted": admitted,
            "decision": "admit" if admitted else "reject",
            "reason": reason,
            "reason_text": message,
            "source": source,
            "site": site,
            "fingerprint": fingerprint,
            "list_version": snapshot.version,
            "list_effective_at": snapshot.effective_at,
            "audit": audit,
        }


# -- 模块级默认准入器（保持 admit(task) / reload() 接口） -----------------

_default_guard: Optional[Guard] = None
_default_lock = threading.Lock()


def _guard() -> Guard:
    global _default_guard
    if _default_guard is None:
        with _default_lock:
            if _default_guard is None:
                _default_guard = Guard()
    return _default_guard


def reset() -> None:
    """丢弃默认准入器的名单快照、指纹与审计（主要供测试/演示使用）。"""
    global _default_guard
    with _default_lock:
        _default_guard = None


def admit(task: Any) -> dict[str, Any]:
    return _guard().admit(task)


def reload(path: Optional[str | os.PathLike[str]] = None) -> dict[str, Any]:
    return _guard().reload(path)


def configure(path: str | os.PathLike[str]) -> dict[str, Any]:
    """为默认准入器指定名单文件并立即加载。"""
    return _guard().reload(path)


def status() -> dict[str, Any]:
    return _guard().status()


def summary() -> dict[str, Any]:
    return _guard().summary()


def audit_log() -> list[dict[str, Any]]:
    return _guard().audit_log()


def reload_history() -> list[dict[str, Any]]:
    return _guard().reload_history()


def export_rejections(
    path: Optional[str | os.PathLike[str]] = None,
) -> dict[str, Any]:
    return _guard().export_rejections(path)
