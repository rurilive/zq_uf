"""按页面/表单/步骤分域的表单令牌池。

作用域键为 (page, step)；page 可写成 "页面:表单" 以区分同页多个表单。
同一作用域并发获取只抓取一次（单飞），其余线程等待并复用结果；
令牌带过期时间和一次性标记；刷新失败回退上一枚有效令牌并告警。
"""

import itertools
import logging
import threading
import time

__all__ = [
    "TokenPool",
    "TokenError",
    "TokenExpiredError",
    "FetchError",
    "get",
    "consume",
    "refresh",
    "invalidate",
    "stats",
    "configure",
    "reset",
]

_log = logging.getLogger("csrftoken")


class TokenError(Exception):
    """令牌池基础异常。"""


class TokenExpiredError(TokenError):
    """令牌已过期，不能消费。"""


class FetchError(TokenError):
    """抓取失败且没有有效回退令牌。"""


class _Record:
    __slots__ = ("token", "expires_at", "consumed")

    def __init__(self, token, expires_at):
        self.token = token
        self.expires_at = expires_at
        self.consumed = False


class TokenPool:
    """分域令牌池。fetcher(page, step) -> 令牌字符串，失败时抛异常。"""

    def __init__(self, fetcher=None, ttl=300.0, clock=None):
        self._seq = itertools.count(1)
        self._fetcher = fetcher or self._default_fetcher
        self._ttl = float(ttl)
        if self._ttl <= 0:
            raise ValueError("ttl 必须为正数秒，收到: %r" % (ttl,))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._scopes = {}        # (page, step) -> _Record 当前令牌
        self._previous = {}      # (page, step) -> _Record 上一枚令牌（回退用）
        self._inflight = {}      # (page, step) -> threading.Event 单飞标记
        self._fetch_errors = {}  # (page, step) -> FetchError 本轮抓取错误
        self._hits = 0
        self._refetches = 0
        self._fallbacks = 0
        self._alerts = []

    # -- 内部工具 ------------------------------------------------------

    def _default_fetcher(self, page, step):
        return "t%d" % next(self._seq)

    @staticmethod
    def _key(page, step):
        if not isinstance(page, str) or not page:
            raise ValueError(
                "page 必须是非空字符串（可写 '页面:表单' 区分同页多表单），收到: %r" % (page,))
        if not isinstance(step, int) or isinstance(step, bool) or step < 1:
            raise ValueError("step 必须是 >= 1 的整数，收到: %r" % (step,))
        return (page, step)

    def _expired(self, rec):
        return self._clock() >= rec.expires_at

    def _usable(self, rec):
        return not rec.consumed and not self._expired(rec)

    @staticmethod
    def _view(page, step, rec):
        return {"page": page, "step": step, "token": rec.token,
                "expires_at": rec.expires_at}

    def _alert(self, message):
        self._alerts.append(message)
        _log.warning("%s", message)

    # -- 抓取与回退 ----------------------------------------------------

    def _refetch(self, key, page, step):
        """抓取新令牌；失败时回退上一枚有效令牌，无回退则记录并抛 FetchError。"""
        try:
            value = self._fetcher(page, step)
            if not isinstance(value, str) or not value:
                raise ValueError("抓取器返回了非法令牌: %r" % (value,))
        except Exception as exc:
            with self._lock:
                prev = self._previous.get(key)
                if prev is not None and self._usable(prev):
                    self._scopes[key] = prev
                    self._previous.pop(key, None)
                    self._fallbacks += 1
                    self._alert(
                        "页面 %s 步骤 %s 刷新失败，回退上一枚有效令牌: %s"
                        % (page, step, exc))
                    self._fetch_errors.pop(key, None)
                    return
                err = FetchError(
                    "页面 %s 步骤 %s 抓取令牌失败且无有效回退: %s"
                    % (page, step, exc))
                self._fetch_errors[key] = err
                self._alert(str(err))
            raise err from exc
        with self._lock:
            self._refetches += 1
            old = self._scopes.get(key)
            if old is not None:
                self._previous[key] = old
            self._scopes[key] = _Record(value, self._clock() + self._ttl)
            self._fetch_errors.pop(key, None)

    # -- 对外接口 ------------------------------------------------------

    def get(self, page, step):
        """取作用域 (page, step) 的令牌；并发同域只抓一次，其余等待复用。"""
        key = self._key(page, step)
        with self._lock:
            rec = self._scopes.get(key)
            if rec is not None and self._usable(rec):
                self._hits += 1
                return self._view(page, step, rec)
            event = self._inflight.get(key)
            if event is None:
                event = threading.Event()
                self._inflight[key] = event
                leader = True
            else:
                leader = False
        if leader:
            try:
                self._refetch(key, page, step)
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
                    event.set()
            with self._lock:
                rec = self._scopes.get(key)
                if rec is not None and self._usable(rec):
                    return self._view(page, step, rec)
                err = self._fetch_errors.get(key)
            if err is not None:
                raise err
            raise FetchError("页面 %s 步骤 %s 抓取后没有可用令牌" % (page, step))
        event.wait()
        with self._lock:
            rec = self._scopes.get(key)
            if rec is not None and self._usable(rec):
                self._hits += 1
                return self._view(page, step, rec)
            err = self._fetch_errors.get(key)
        if err is not None:
            raise err
        # 极少出现：领导者结束后记录仍不可用且无错误，重走入口。
        return self.get(page, step)

    def consume(self, page, step):
        """一次性消费当前令牌。并发调用只放行一个（返回 True），其余 False。"""
        key = self._key(page, step)
        with self._lock:
            rec = self._scopes.get(key)
            if rec is None:
                raise TokenError(
                    "页面 %s 步骤 %s 没有可消费的令牌，请先 get()" % (page, step))
            if rec.consumed:
                return False
            if self._expired(rec):
                raise TokenExpiredError(
                    "页面 %s 步骤 %s 的令牌已过期，请重新 get()" % (page, step))
            rec.consumed = True
            return True

    def refresh(self, page, step):
        """强制重取；失败时回退上一枚有效令牌并告警，无回退则抛 FetchError。"""
        key = self._key(page, step)
        with self._lock:
            old = self._scopes.pop(key, None)
            if old is not None:
                self._previous[key] = old
        return self.get(page, step)

    def invalidate(self, page):
        """使该页面所有步骤的令牌（含回退）失效。"""
        if not isinstance(page, str) or not page:
            raise ValueError("page 必须是非空字符串，收到: %r" % (page,))
        with self._lock:
            for store in (self._scopes, self._previous, self._fetch_errors):
                for key in [k for k in store if k[0] == page]:
                    store.pop(key, None)
        return True

    def stats(self):
        """命中、重取、回退次数和告警列表。"""
        with self._lock:
            return {"hits": self._hits, "refetches": self._refetches,
                    "fallbacks": self._fallbacks, "alerts": list(self._alerts)}


# -- 模块级默认池 -------------------------------------------------------

_pool = TokenPool()


def configure(fetcher=None, ttl=300.0, clock=None):
    """重建模块级默认池（清空全部作用域与统计）。"""
    global _pool
    _pool = TokenPool(fetcher=fetcher, ttl=ttl, clock=clock)


def reset():
    """清空默认池，恢复默认抓取器。"""
    configure()


def get(page, step):
    return _pool.get(page, step)


def consume(page, step):
    return _pool.consume(page, step)


def refresh(page, step):
    return _pool.refresh(page, step)


def invalidate(page):
    return _pool.invalidate(page)


def stats():
    return _pool.stats()
