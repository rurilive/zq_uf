"""按主机+端口缓存 robots 规则。

- 每条链接不再重新拉取：规则按 (scheme, host, port) 缓存，带抓取时间和 TTL。
- 过期后后台刷新，本次仍用旧规则返回；刷新失败保留旧规则并记录告警。
- 子域按自己的 robots 判断；子域没有 robots 时继承主域规则。
- 规则更新只影响新请求：enqueue 固定版本，已入队链接按旧版本完成。
- 全量更新前用 validate_update 在测试链接上对比新旧拒绝范围，一致才启用。
"""

from __future__ import annotations

import threading
import time
import urllib.parse
import urllib.robotparser
import urllib.request

DEFAULT_TTL = 3600.0
FETCH_TIMEOUT = 5.0
DEFAULT_AGENT = "*"


def _key_for(url):
    p = urllib.parse.urlparse(url if "://" in url else "http://" + url)
    port = p.port or (443 if p.scheme == "https" else 80)
    return (p.scheme, p.hostname or "", port)


def _parent_host(host):
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) > 2 else None


class _Rules:
    """一份不可变的规则快照；旧版本保留给已入队链接。"""

    __slots__ = ("parser", "fetched_at", "version", "source")

    def __init__(self, parser, fetched_at, version, source):
        self.parser = parser          # None 表示无规则（放行）
        self.fetched_at = fetched_at
        self.version = version
        self.source = source          # "own" | "inherited:<主域>" | "none"

    def allows(self, agent, url):
        if self.parser is None:
            return True, "no robots rules, allowed"
        if self.parser.can_fetch(agent, url):
            return True, "allowed by robots.txt (%s)" % self.source
        return False, "disallowed by robots.txt (%s)" % self.source


class _Entry:
    __slots__ = ("current", "history", "refreshing")

    def __init__(self):
        self.current = None   # 当前 _Rules
        self.history = {}     # version -> _Rules，供已入队链接按旧规则完成
        self.refreshing = False


class RobotsCache:
    def __init__(self, fetcher=None, ttl=DEFAULT_TTL, agent=DEFAULT_AGENT, clock=time.time):
        self._fetch = fetcher or self._urllib_fetch
        self._ttl = ttl
        self._agent = agent
        self._clock = clock
        self._entries = {}
        self._alerts = []
        self._lock = threading.Lock()
        self._version = 0

    # ---------- 抓取 ----------

    @staticmethod
    def _urllib_fetch(url):
        try:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # 没有 robots.txt
            raise

    def _fetch_rules(self, key):
        """返回 (parser, source)；子域无 robots 时回退主域。"""
        scheme, host, port = key
        text = self._fetch("%s://%s:%d/robots.txt" % (scheme, host, port))
        source = "own"
        if text is None:
            parent = _parent_host(host)
            if parent:
                text = self._fetch("%s://%s:%d/robots.txt" % (scheme, parent, port))
                source = "inherited:%s" % parent if text is not None else "none"
            else:
                source = "none"
        parser = None
        if text is not None:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(text.splitlines())
        return parser, source

    # ---------- 存储 ----------

    def _next_version(self):
        with self._lock:
            self._version += 1
            return self._version

    def _store(self, key, rules):
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            if entry.current is not None:
                entry.history[entry.current.version] = entry.current
            entry.current = rules

    def _alert(self, key, message):
        with self._lock:
            self._alerts.append({
                "host": key[1], "port": key[2],
                "error": message, "time": self._clock(),
                "action": "kept-old-rules",
            })

    def alerts(self):
        """刷新/校验失败的告警；旧规则均保留。"""
        with self._lock:
            return list(self._alerts)

    # ---------- 刷新 ----------

    def _load_sync(self, key):
        """首次抓取（缓存里还没有任何规则可用时）。"""
        try:
            parser, source = self._fetch_rules(key)
        except Exception as exc:  # 没有旧规则可退，放行并告警
            self._alert(key, "initial fetch failed: %s" % exc)
            parser, source = None, "none"
        rules = _Rules(parser, self._clock(), self._next_version(), source)
        self._store(key, rules)
        return rules

    def _schedule_refresh(self, key):
        with self._lock:
            entry = self._entries[key]
            if entry.refreshing:
                return False
            entry.refreshing = True
        threading.Thread(target=self._refresh_worker, args=(key,), daemon=True).start()
        return True

    def _refresh_worker(self, key):
        try:
            parser, source = self._fetch_rules(key)
        except Exception as exc:
            self._alert(key, "refresh failed: %s" % exc)  # 保留旧规则
        else:
            self._store(key, _Rules(parser, self._clock(), self._next_version(), source))
        finally:
            with self._lock:
                self._entries[key].refreshing = False

    def refresh(self, host, scheme="https", port=None):
        """同步强制刷新；失败保留旧规则并告警。"""
        key = (scheme, host, port or (443 if scheme == "https" else 80))
        try:
            parser, source = self._fetch_rules(key)
        except Exception as exc:
            self._alert(key, "refresh failed: %s" % exc)
            with self._lock:
                entry = self._entries.get(key)
            return {"host": host, "refreshed": False,
                    "version": entry.current.version if entry and entry.current else None,
                    "error": str(exc)}
        rules = _Rules(parser, self._clock(), self._next_version(), source)
        self._store(key, rules)
        return {"host": host, "refreshed": True, "version": rules.version, "source": source}

    # ---------- 查询 ----------

    def _ensure(self, key):
        with self._lock:
            entry = self._entries.get(key)
        if entry is None or entry.current is None:
            self._load_sync(key)

    def allowed(self, url, agent=None, version=None):
        """判断 url 是否可抓。version 固定时按该版本旧规则判断（已入队链接）。"""
        key = _key_for(url)
        agent = agent or self._agent
        self._ensure(key)
        with self._lock:
            entry = self._entries[key]
            current = entry.current
            expired = (self._clock() - current.fetched_at) > self._ttl
            if version is not None and current.version != version:
                rules = entry.history.get(version, current)  # 已入队链接按旧规则
            else:
                rules = current
        refreshed = False
        if version is None and expired:
            refreshed = self._schedule_refresh(key)  # 后台刷新，本次仍用旧规则
        ok, reason = rules.allows(agent, url)
        return {
            "url": url,
            "allowed": ok,
            "host": key[1],
            "port": key[2],
            "cached": True,          # 命中缓存（规则来自缓存而非本次拉取）
            "refreshed": refreshed,  # 本次是否触发了后台刷新
            "expired": expired,      # 当前规则是否已过期
            "reason": reason,        # 放行/拒绝原因
            "source": rules.source,
            "version": rules.version,
        }

    # ---------- 队列语义：规则更新只影响新请求 ----------

    def enqueue(self, url):
        """入队：固定当前规则版本。"""
        key = _key_for(url)
        self._ensure(key)
        with self._lock:
            version = self._entries[key].current.version
        return {"url": url, "host": key[1], "version": version}

    def allowed_queued(self, ticket, agent=None):
        """已入队链接按入队时的旧规则完成。"""
        return self.allowed(ticket["url"], agent=agent, version=ticket["version"])

    # ---------- 灰度：先在测试链接上验证再全量 ----------

    def validate_update(self, host, test_urls, scheme="https", port=None, agent=None):
        """抓取新规则，对比新旧规则在 test_urls 上的拒绝范围；一致才启用，否则保留旧规则并告警。"""
        agent = agent or self._agent
        key = (scheme, host, port or (443 if scheme == "https" else 80))
        self._ensure(key)
        with self._lock:
            old = self._entries[key].current
        try:
            parser, source = self._fetch_rules(key)
        except Exception as exc:
            self._alert(key, "validation fetch failed: %s" % exc)
            return {"host": host, "promoted": False, "error": str(exc),
                    "version": old.version}
        new = _Rules(parser, self._clock(), self._next_version(), source)
        old_rejected = sorted(u for u in test_urls if not old.allows(agent, u)[0])
        new_rejected = sorted(u for u in test_urls if not new.allows(agent, u)[0])
        if old_rejected != new_rejected:
            self._alert(key, "validation failed: rejection scope differs, kept old rules")
            return {"host": host, "promoted": False, "version": old.version,
                    "old_rejected": old_rejected, "new_rejected": new_rejected}
        self._store(key, new)
        return {"host": host, "promoted": True, "version": new.version,
                "rejected": new_rejected}


_default = RobotsCache()


def allowed(url, agent=None):
    return _default.allowed(url, agent=agent)


def refresh(host, **kw):
    return _default.refresh(host, **kw)


def enqueue(url):
    return _default.enqueue(url)


def allowed_queued(ticket, agent=None):
    return _default.allowed_queued(ticket, agent=agent)


def validate_update(host, test_urls, **kw):
    return _default.validate_update(host, test_urls, **kw)


def alerts():
    return _default.alerts()
