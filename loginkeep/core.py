"""账号级登录态池。

要点：

* 同一账号在同一份存储上共享同一令牌（多进程 / 多机共用存储即可）。
* 令牌到期前由后台线程提前续期；前台只在必要时兜底续期。
* 令牌被踢后只重登一次，重复请求复用这次重登的结果。
* 登录失败按账号暂停；账号问题与站点问题使用不同的暂停时长。
* 暂停结束后下一次 ``lease`` 自动试探登录，先限流，连续成功后再恢复并发。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from contextlib import contextmanager

from .errors import AccountError, KickedError, LoginError, SiteError
from .simulator import RealClock


def _new_state(account: str) -> dict:
    return {
        "account": account,
        "token": None,
        "token_via": None,
        "issued_at": None,
        "expires_at": 0.0,
        "lead": 0.0,
        "refresh_count": 0,
        "relogin_count": 0,
        "refreshed_at": None,
        "relogin_at": None,
        "relogin_times": [],
        "refresh_failures": 0,
        "refresh_retry_at": 0.0,
        "paused": False,
        "pause_until": None,
        "failures": 0,
        "last_error": None,
        "probation": False,
        "streak": 0,
        "retired_tokens": [],
    }


def _normalize(state: dict | None) -> dict | None:
    """补齐旧版本磁盘状态缺少的字段。"""
    if not isinstance(state, dict):
        return None
    state = copy.deepcopy(state)
    state.pop("normal_inflight", None)
    for key, value in _new_state(str(state.get("account", ""))).items():
        state.setdefault(key, value)
    for key in ("retired_tokens", "relogin_times"):
        if not isinstance(state[key], list):
            state[key] = []
    return state


class MemoryStore:
    """进程内共享存储；同一个实例可被多个 :class:`SessionPool` 共用。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._state = {"accounts": {}, "tokens": {}}
        self._account_locks: dict[str, threading.RLock] = {}

    @contextmanager
    def account_lock(self, account: str):
        with self._lock:
            lock = self._account_locks.get(account)
            if lock is None:
                lock = self._account_locks[account] = threading.RLock()
        with lock:
            yield

    def get_account(self, account: str) -> dict | None:
        with self._lock:
            state = self._state["accounts"].get(account)
            return _normalize(state) if state is not None else None

    def put_account(self, account: str, state: dict) -> None:
        with self._lock:
            self._state["accounts"][account] = copy.deepcopy(state)
            self._reindex(account)

    def find_account(self, token: str) -> str | None:
        with self._lock:
            return self._state["tokens"].get(token)

    def accounts(self) -> list[str]:
        with self._lock:
            return list(self._state["accounts"])

    def _reindex(self, account: str) -> None:
        tokens = self._state["tokens"]
        for token, owner in list(tokens.items()):
            if owner == account:
                del tokens[token]
        state = self._state["accounts"][account]
        for token in [state["token"]] + state["retired_tokens"]:
            if token:
                tokens[token] = account


class FileStore(MemoryStore):
    """JSON + ``flock`` 存储，供多进程 / 共用挂载点的多台机器共享登录态。

    ``path`` 指向共享目录。每个账号有独立的锁文件，登录/续期只在同账号内串行。
    """

    def __init__(self, path: str):
        super().__init__()
        self.path = os.path.abspath(path)
        os.makedirs(self.path, exist_ok=True)
        self._state_file = os.path.join(self.path, "state.json")
        self._state_lock_file = os.path.join(self.path, "state.lock")
        self._locks_dir = os.path.join(self.path, "locks")
        os.makedirs(self._locks_dir, exist_ok=True)
        self._flock_ok = self._init_flock()

    @staticmethod
    def _init_flock() -> bool:
        try:
            import fcntl  # noqa: F401
        except ImportError:  # pragma: no cover - Windows
            return False
        return True

    @contextmanager
    def _file_lock(self, path: str):
        if not self._flock_ok:  # pragma: no cover
            yield
            return
        import fcntl

        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load_locked(self) -> dict:
        try:
            with open(self._state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            data = {}
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("accounts"), dict):
            data["accounts"] = {}
        if not isinstance(data.get("tokens"), dict):
            data["tokens"] = {}
        return data

    def _save_locked(self, data: dict) -> None:
        tmp = self._state_file + ".tmp.%d.%d" % (os.getpid(), threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._state_file)

    @contextmanager
    def account_lock(self, account: str):
        with super().account_lock(account):
            name = hashlib.sha1(account.encode("utf-8")).hexdigest()[:16] + ".lock"
            with self._file_lock(os.path.join(self._locks_dir, name)):
                yield

    def get_account(self, account: str) -> dict | None:
        with self._file_lock(self._state_lock_file):
            data = self._load_locked()
            state = data["accounts"].get(account)
        return _normalize(state) if state is not None else None

    def put_account(self, account: str, state: dict) -> None:
        with self._file_lock(self._state_lock_file):
            data = self._load_locked()
            data["accounts"][account] = copy.deepcopy(state)
            tokens = data["tokens"]
            for token, owner in list(tokens.items()):
                if owner == account:
                    del tokens[token]
            for token in [state["token"]] + state["retired_tokens"]:
                if token:
                    tokens[token] = account
            self._save_locked(data)

    def find_account(self, token: str) -> str | None:
        with self._file_lock(self._state_lock_file):
            return self._load_locked()["tokens"].get(token)

    def accounts(self) -> list[str]:
        with self._file_lock(self._state_lock_file):
            return list(self._load_locked()["accounts"])


class SessionPool:
    """账号登录态池；模块级 ``lease`` / ``refresh`` 使用默认实例。"""

    def __init__(
        self,
        site,
        clock=None,
        store=None,
        *,
        refresh_ratio: float = 0.25,
        refresh_max_lead: float = 300.0,
        account_pause: float = 1800.0,
        account_pause_max: float = 6 * 3600.0,
        site_pause: float = 30.0,
        site_pause_max: float = 900.0,
        refresh_soft_grace: float = 5.0,
        relogin_window: float = 600.0,
        relogin_max: int = 3,
        probation_successes: int = 3,
        normal_inflight: int = 4,
        background: bool | None = None,
        background_interval: float = 1.0,
    ):
        for method in ("login", "refresh"):
            if not callable(getattr(site, method, None)):
                raise TypeError("site must implement %s()" % method)
        self._site = site
        self._clock = clock or RealClock()
        self._store = store or MemoryStore()
        self._refresh_ratio = float(refresh_ratio)
        self._refresh_max_lead = float(refresh_max_lead)
        self._account_pause = float(account_pause)
        self._account_pause_max = float(account_pause_max)
        self._site_pause = float(site_pause)
        self._site_pause_max = float(site_pause_max)
        self._refresh_soft_grace = float(refresh_soft_grace)
        self._relogin_window = float(relogin_window)
        self._relogin_max = max(1, int(relogin_max))
        self._probation_successes = max(1, int(probation_successes))
        self._normal_inflight = max(1, int(normal_inflight))
        self._background_interval = float(background_interval)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        if background is None:
            background = not getattr(self._clock, "manual", False)
        if background:
            self.start_background()

    # ---- 对外接口 ---------------------------------------------------
    def lease(self, account: str) -> dict:
        """取该账号的共享登录态；需要时登录、续期或重登。"""
        account = str(account)
        with self._store.account_lock(account):
            now = self._clock.now()
            state = self._store.get_account(account) or _new_state(account)
            paused = self._resume_or_pause(state, now)
            if paused is not None:
                return paused

            if self._usable(state, now):
                if self._needs_refresh(state, now):
                    return self._after_success(
                        state, self._refresh_locked(account, state, now, deduped=False)
                    )
                return self._after_success(
                    state,
                    self._ok_result(
                        state, now, shared=True, refreshed=False, relogin=False
                    ),
                )

            return self._after_success(
                state, self._login_locked(account, state, now, deduped=False)
            )

    def refresh(self, token: str) -> dict:
        """续期令牌；若令牌已被踢，则只重登一次。

        这是显式调用：调用方通常是因为手里的令牌出了问题才来刷新，
        所以总会向站点验证一次，从而能发现「被踢」并触发单次重登。
        临近过期的续期走 :meth:`lease` 或后台 tick，不需要调用本方法。
        """
        token = str(token)
        account = self._store.find_account(token)
        if account is None:
            return self._unknown_result(token)

        with self._store.account_lock(account):
            now = self._clock.now()
            state = self._store.get_account(account) or _new_state(account)
            paused = self._resume_or_pause(state, now)
            if paused is not None:
                return paused

            if state["token"] != token:
                # 令牌已被别的请求刷新/重登替换：直接复用，不再发起续期。
                deduped = True
                placed = state["token_via"]
                if self._usable(state, now):
                    return self._after_success(
                        state,
                        self._ok_result(
                            state,
                            now,
                            shared=True,
                            refreshed=placed == "refresh",
                            relogin=placed == "relogin",
                            deduped=deduped,
                        ),
                    )
                return self._after_success(
                    state,
                    self._login_locked(account, state, now, deduped=deduped),
                )

            if not self._usable(state, now):
                return self._after_success(
                    state, self._login_locked(account, state, now, deduped=False)
                )
            return self._after_success(
                state, self._refresh_locked(account, state, now, deduped=False)
            )

    def tick(self) -> None:
        """后台维护一轮。

        * 暂停到期：自动做一次小流量试探登录，恢复接单，无需人工重启。
        * 临近过期：提前续期，前台请求直接拿到新令牌。
        """
        for account in self._store.accounts():
            with self._store.account_lock(account):
                now = self._clock.now()
                state = self._store.get_account(account)
                if state is None:
                    continue
                was_paused = bool(state["paused"])
                if self._resume_or_pause(state, now) is not None:
                    continue  # 仍在暂停期
                if was_paused:
                    # 暂停已到期：后台小流量试探登录，恢复后自动接单。
                    self._after_success(
                        state, self._login_locked(account, state, now, deduped=False)
                    )
                    continue
                if self._usable(state, now) and self._needs_refresh(state, now):
                    self._after_success(
                        state, self._refresh_locked(account, state, now, deduped=False)
                    )

    def status(self, account: str) -> dict:
        """查看账号当前状态，不发起登录。"""
        account = str(account)
        with self._store.account_lock(account):
            now = self._clock.now()
            state = self._store.get_account(account)
            if state is None:
                return {
                    "account": account,
                    "status": "unknown",
                    "token": None,
                    "token_via": None,
                    "shared": False,
                    "expires_in": 0.0,
                    "expires_at": None,
                    "refreshed": False,
                    "relogin": False,
                    "refresh_count": 0,
                    "relogin_count": 0,
                    "refreshed_at": None,
                    "relogin_at": None,
                    "paused_until": None,
                    "retry_after": 0.0,
                    "error": None,
                    "probation": False,
                    "rate": {"max_inflight": 0, "probation": False},
                    "deduped": False,
                }
            paused = self._resume_or_pause(state, now, commit=False)
            if paused is not None:
                return paused
            if self._usable(state, now):
                return self._ok_result(
                    state, now, shared=True, refreshed=False, relogin=False
                )
            probation = bool(state["probation"] or state["paused"])
            return {
                "account": account,
                "status": "idle",
                "token": None,
                "expires_in": 0.0,
                "expires_at": None,
                "shared": False,
                "refreshed": False,
                "relogin": False,
                "refresh_count": state["refresh_count"],
                "relogin_count": state["relogin_count"],
                "paused_until": None,
                "retry_after": 0.0,
                "error": state["last_error"],
                "probation": probation,
                "rate": {
                    "max_inflight": 1 if probation else self._normal_inflight,
                    "probation": probation,
                },
                "deduped": False,
            }

    def start_background(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop,
            name="loginkeep-refresh",
            daemon=True,
        )
        self._thread.start()

    def stop_background(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    close = stop_background

    def _background_loop(self) -> None:
        while not self._stop_event.wait(self._background_interval):
            try:
                self.tick()
            except Exception:  # 后台线程不能因单次异常退出
                pass

    # ---- 内部逻辑 ---------------------------------------------------
    def _resume_or_pause(self, state: dict, now: float, *, commit: bool = True) -> dict | None:
        """暂停中返回暂停结果；暂停到期则进入试探期。

        试探期内不复用旧令牌，下一次 ``lease`` 会做一次真正的试探登录。
        ``commit=False`` 用于只读查询：返回相同结论但不改写状态。
        """
        if not state["paused"]:
            return None
        if now < (state["pause_until"] or 0.0):
            return self._paused_result(state, now)
        if not commit:
            return None
        state["paused"] = False
        state["pause_until"] = None
        state["probation"] = True
        state["streak"] = 0
        state["token"] = None
        state["token_via"] = None
        state["expires_at"] = 0.0
        state["last_error"] = None
        state["refresh_failures"] = 0
        state["refresh_retry_at"] = 0.0
        self._store.put_account(state["account"], state)
        return None

    def _usable(self, state: dict, now: float) -> bool:
        return bool(state["token"]) and now < state["expires_at"]

    def _needs_refresh(self, state: dict, now: float) -> bool:
        if now < state["refresh_retry_at"]:
            return False
        return now >= state["expires_at"] - state["lead"]

    def _lead(self, ttl: float) -> float:
        return max(0.1, min(ttl * self._refresh_ratio, self._refresh_max_lead))

    def _login_locked(
        self, account: str, state: dict, now: float, *, deduped: bool
    ) -> dict:
        try:
            result = self._site.login(account)
        except AccountError as exc:
            return self._fail_locked(account, state, now, "account", exc)
        except KickedError as exc:  # 登录入口一般不会抛这个，按站点问题处理
            return self._fail_locked(account, state, now, "site", exc)
        except LoginError as exc:
            return self._fail_locked(account, state, now, "site", exc)
        except Exception as exc:  # 未预期的实现错误也归为站点侧
            return self._fail_locked(account, state, now, "site", exc)
        self._apply_issue(state, result, now, refreshed=False, relogin=False)
        return self._ok_result(
            state, now, shared=False, refreshed=False, relogin=False,
            deduped=deduped,
        )

    def _refresh_locked(
        self, account: str, state: dict, now: float, *, deduped: bool
    ) -> dict:
        token = state["token"]
        try:
            result = self._site.refresh(token)
        except KickedError:
            self._retire(state, token)
            return self._relogin_locked(account, state, now, deduped=deduped)
        except AccountError as exc:
            return self._fail_locked(account, state, now, "account", exc)
        except LoginError as exc:
            delta = state["expires_at"] - now
            if delta > self._refresh_soft_grace:
                # 令牌还有效，续期暂时失败不影响使用：退避后再试，不停账号。
                return self._soft_refresh_failure(state, now, exc)
            return self._fail_locked(account, state, now, "site", exc)
        except Exception as exc:
            delta = state["expires_at"] - now
            if delta > self._refresh_soft_grace:
                return self._soft_refresh_failure(state, now, exc)
            return self._fail_locked(account, state, now, "site", exc)
        state["refresh_failures"] = 0
        state["refresh_retry_at"] = 0.0
        self._apply_issue(state, result, now, refreshed=True, relogin=False)
        return self._ok_result(
            state, now, shared=True, refreshed=True, relogin=False, deduped=deduped
        )

    def _soft_refresh_failure(
        self, state: dict, now: float, exc: BaseException
    ) -> dict:
        state["refresh_failures"] += 1
        backoff = min(
            self._site_pause_max,
            self._site_pause * (2 ** (state["refresh_failures"] - 1)),
        )
        state["refresh_retry_at"] = now + backoff
        self._store.put_account(state["account"], state)
        result = self._ok_result(
            state, now, shared=True, refreshed=False, relogin=False
        )
        result["refresh_error"] = {
            "kind": "site",
            "type": type(exc).__name__,
            "message": str(exc),
            "retry_in": backoff,
        }
        return result

    def _relogin_locked(
        self, account: str, state: dict, now: float, *, deduped: bool
    ) -> dict:
        window = [t for t in state["relogin_times"] if now - t < self._relogin_window]
        state["relogin_times"] = window
        if len(window) >= self._relogin_max:
            # 短时间内反复被踢：停止重登，按站点问题暂停，避免形成踢-重登死循环。
            return self._fail_locked(
                account,
                state,
                now,
                "site",
                SiteError(
                    "kicked %d times within %.0fs" % (len(window), self._relogin_window)
                ),
            )
        try:
            result = self._site.login(account)
        except AccountError as exc:
            return self._fail_locked(account, state, now, "account", exc)
        except LoginError as exc:
            return self._fail_locked(account, state, now, "site", exc)
        except Exception as exc:
            return self._fail_locked(account, state, now, "site", exc)
        state["relogin_times"] = window + [now]
        self._apply_issue(state, result, now, refreshed=False, relogin=True)
        return self._ok_result(
            state, now, shared=False, refreshed=False, relogin=True,
            deduped=deduped,
        )

    def _apply_issue(
        self, state: dict, result, now: float, *, refreshed: bool, relogin: bool
    ) -> None:
        old = state["token"]
        if old and old != result.token:
            self._retire(state, old)
        state["token"] = result.token
        state["token_via"] = "refresh" if refreshed else ("relogin" if relogin else "login")
        state["issued_at"] = now
        state["expires_at"] = now + float(result.ttl)
        state["lead"] = self._lead(float(result.ttl))
        state["paused"] = False
        state["pause_until"] = None
        state["last_error"] = None
        state["failures"] = 0  # 连续失败计数：成功即清零
        state["refresh_failures"] = 0
        state["refresh_retry_at"] = 0.0
        if refreshed:
            state["refresh_count"] += 1
            state["refreshed_at"] = now
        if relogin:
            state["relogin_count"] += 1
            state["relogin_at"] = now
        self._store.put_account(state["account"], state)

    def _after_success(self, state: dict, result: dict) -> dict:
        """每次成功使用登录态都计入试探期进度。"""
        if result["status"] != "ok":
            return result
        if state["probation"] and not result.get("refresh_error"):
            state["streak"] += 1
            if state["streak"] >= self._probation_successes:
                state["probation"] = False
                state["streak"] = 0
                state["failures"] = 0
            self._store.put_account(state["account"], state)
        probation = bool(state["probation"])
        result["probation"] = probation
        result["rate"] = {
            "max_inflight": 1 if probation else self._normal_inflight,
            "probation": probation,
        }
        return result

    def _retire(self, state: dict, token: str | None) -> None:
        """保留少量旧令牌到索引里，便于把陈旧令牌映射回账号。"""
        if not token:
            return
        retired = state["retired_tokens"]
        if token not in retired:
            retired.append(token)
        del retired[:-7]

    def _fail_locked(
        self,
        account: str,
        state: dict,
        now: float,
        kind: str,
        exc: BaseException,
    ) -> dict:
        state["failures"] += 1
        if kind == "account":
            base, cap = self._account_pause, self._account_pause_max
        else:
            base, cap = self._site_pause, self._site_pause_max
        pause = min(cap, base * (2 ** (state["failures"] - 1)))
        state["paused"] = True
        state["pause_until"] = now + pause
        state["probation"] = False
        state["streak"] = 0
        state["last_error"] = {
            "kind": kind,
            "type": type(exc).__name__,
            "message": str(exc),
        }
        self._store.put_account(account, state)
        return self._paused_result(state, now)

    def _ok_result(
        self,
        state: dict,
        now: float,
        *,
        shared: bool,
        refreshed: bool,
        relogin: bool,
        deduped: bool = False,
    ) -> dict:
        probation = bool(state["probation"])
        return {
            "account": state["account"],
            "status": "ok",
            "token": state["token"],
            "token_via": state["token_via"],
            "shared": shared,
            "expires_in": max(0.0, state["expires_at"] - now),
            "expires_at": state["expires_at"],
            "refreshed": refreshed,
            "relogin": relogin,
            "refresh_count": state["refresh_count"],
            "relogin_count": state["relogin_count"],
            "refreshed_at": state["refreshed_at"],
            "relogin_at": state["relogin_at"],
            "paused_until": None,
            "retry_after": 0.0,
            "error": None,
            "probation": probation,
            "rate": {
                "max_inflight": 1 if probation else self._normal_inflight,
                "probation": probation,
            },
            "deduped": deduped,
        }

    def _paused_result(self, state: dict, now: float) -> dict:
        pause_until = state["pause_until"] or now
        return {
            "account": state["account"],
            "status": "paused",
            "token": None,
            "token_via": None,
            "shared": False,
            "expires_in": 0.0,
            "expires_at": None,
            "refreshed": False,
            "relogin": False,
            "refresh_count": state["refresh_count"],
            "relogin_count": state["relogin_count"],
            "refreshed_at": state["refreshed_at"],
            "relogin_at": state["relogin_at"],
            "paused_until": pause_until,
            "retry_after": max(0.0, pause_until - now),
            "error": state["last_error"],
            "probation": False,
            "rate": {"max_inflight": 0, "probation": False},
            "deduped": False,
        }

    @staticmethod
    def _unknown_result(token: str) -> dict:
        return {
            "account": None,
            "status": "unknown",
            "token": None,
            "token_via": None,
            "shared": False,
            "expires_in": 0.0,
            "expires_at": None,
            "refreshed": False,
            "relogin": False,
            "refresh_count": 0,
            "relogin_count": 0,
            "refreshed_at": None,
            "relogin_at": None,
            "paused_until": None,
            "retry_after": 0.0,
            "error": {
                "kind": "unknown",
                "type": "UnknownToken",
                "message": "token %r is not tracked" % token,
            },
            "probation": False,
            "rate": {"max_inflight": 0, "probation": False},
            "deduped": False,
        }


# ---- 模块级默认实例 --------------------------------------------------
_default_lock = threading.Lock()
_default_pool: SessionPool | None = None


def configure(
    site,
    clock=None,
    store=None,
    *,
    background: bool | None = None,
    **options,
) -> SessionPool:
    """配置默认登录态池。

    多台机器共享登录态时传入指向共享目录的 :class:`FileStore`::

        loginkeep.configure(site=site, store=FileStore("/mnt/shared/loginkeep"))
    """
    global _default_pool
    with _default_lock:
        if _default_pool is not None:
            _default_pool.stop_background()
        _default_pool = SessionPool(
            site,
            clock=clock,
            store=store,
            background=background,
            **options,
        )
        return _default_pool


def _pool() -> SessionPool:
    pool = _default_pool
    if pool is None:
        raise RuntimeError(
            "loginkeep is not configured; call loginkeep.configure(site=...) first"
        )
    return pool


def lease(account: str) -> dict:
    return _pool().lease(account)


def refresh(token: str) -> dict:
    return _pool().refresh(token)


def status(account: str) -> dict:
    return _pool().status(account)


__all__ = [
    "MemoryStore",
    "FileStore",
    "SessionPool",
    "configure",
    "lease",
    "refresh",
    "status",
]
