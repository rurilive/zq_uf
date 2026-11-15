"""用于演示和测试的站点/时钟替身。

真实的 ``Site`` 只要实现两个方法即可接入::

    class Site:
        def login(self, account) -> LoginResult: ...
        def refresh(self, token) -> LoginResult: ...

失败时抛出 :class:`~loginkeep.errors.AccountError` 或
:class:`~loginkeep.errors.SiteError`；令牌被踢/失效时抛出
:class:`~loginkeep.errors.KickedError`。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from .errors import AccountError, KickedError, SiteError


@dataclass(frozen=True)
class LoginResult:
    """一次登录/续期的结果。"""

    token: str
    ttl: float


class SimulatedClock:
    """可手动推进的时钟，让暂停/续期在测试和演示中可复现。"""

    manual = True

    def __init__(self, start: float = 1000.0):
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += float(seconds)
        return self._now


class SimulatedSite:
    """内存版站点，支持注入踢下线和登录失败。"""

    def __init__(self, ttl: float = 600.0):
        self.ttl = float(ttl)
        self._lock = threading.RLock()
        self._tokens: dict[str, str] = {}
        self._kicked: set[str] = set()
        self._failures: dict[str, deque[str]] = defaultdict(deque)
        self._seq = 0
        self.login_calls: list[str] = []
        self.refresh_calls: list[str] = []

    # ---- 故障注入 -------------------------------------------------
    def kick(self, account: str) -> None:
        """让该账号当前所有令牌失效，模拟被站点踢下线。"""
        account = str(account)
        with self._lock:
            for token, owner in self._tokens.items():
                if owner == account:
                    self._kicked.add(token)

    def fail_next(self, account: str, kind: str = "account", count: int = 1) -> None:
        """让该账号接下来的 ``count`` 次登录/续期失败。

        ``kind="account"`` 抛账号问题，``kind="site"`` 抛站点问题。
        """
        if kind not in ("account", "site"):
            raise ValueError("kind must be 'account' or 'site'")
        with self._lock:
            self._failures[str(account)].extend([kind] * max(0, int(count)))

    def clear_failures(self, account: str) -> None:
        with self._lock:
            self._failures.pop(str(account), None)

    # ---- Site 协议 ------------------------------------------------
    def login(self, account: str) -> LoginResult:
        account = str(account)
        with self._lock:
            self.login_calls.append(account)
            self._raise_injected(account)
            return self._issue(account)

    def refresh(self, token: str) -> LoginResult:
        token = str(token)
        with self._lock:
            self.refresh_calls.append(token)
            account = self._tokens.get(token)
            if account is None or token in self._kicked:
                raise KickedError("session kicked or unknown")
            self._raise_injected(account)
            # 站点侧续期会换发新令牌
            new = self._issue(account)
            self._tokens[token] = account  # 旧令牌保留映射，便于诊断
            return new

    # ---- 内部 -----------------------------------------------------
    def _issue(self, account: str) -> LoginResult:
        self._seq += 1
        token = "tok-%s-%d" % (account, self._seq)
        self._tokens[token] = account
        return LoginResult(token=token, ttl=self.ttl)

    def _raise_injected(self, account: str) -> None:
        queue = self._failures.get(account)
        if not queue:
            return
        kind = queue.popleft()
        if not queue:
            self._failures.pop(account, None)
        if kind == "account":
            raise AccountError("account rejected by site (risk control)")
        raise SiteError("site temporarily unavailable")


class RealClock:
    """默认时钟；暂停截止时间使用 Unix 时间戳，便于直接展示。"""

    def now(self) -> float:
        return time.time()
