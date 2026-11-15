"""登录态管理器：账号级复用、提前续期、单飞重登与失败熔断。

核心保证（对应接入方反馈的四个故障模式）：

1. **账号级复用**：一个账号在 Store 里只有一份会话，所有 lease 拿到同一
   个令牌（``shared=True``），不再每条请求登录，杜绝多机互踢。
2. **提前续期**：剩余有效期进入续期窗口就静默 renew，在后台完成，
   lease 永远尽量返回没过期的令牌。
3. **踢下线只重登一次**：renew 报告 KickedError 后走跨进程单飞登录，
   并发请求等同一个结果，不会 N 台机器各登一次互相踢。
4. **按账号暂停 + 自动恢复**：失败区分账号/站点问题，按指数退避暂停；
   暂停结束自动转 TRIAL 小流量试探，连续成功才恢复原速，全程无需人工
   重启调度。
"""

import atexit
import threading
import uuid

from .backend import DummyBackend
from .errors import KickedError, LoginError, SiteError
from .store import HEALTHY, InMemoryStore, PAUSED, TRIAL, SessionRecord


class Clock:
    """可注入时钟，测试里用假时钟驱动，线上用墙钟。"""

    def time(self):
        import time

        return time.time()

    def sleep(self, seconds):
        import time

        time.sleep(seconds)


class LoginManager:
    def __init__(
        self,
        backend=None,
        store=None,
        clock=None,
        # 到期前多久开始续期
        renew_margin=300.0,
        # 试探期连续成功多少次后恢复正常速度
        trial_success_needed=3,
        # 账号问题的基础暂停与上限（指数退避）
        account_backoff=300.0,
        account_backoff_max=3600.0,
        # 站点问题通常是暂时的，短暂停
        site_backoff=30.0,
        site_backoff_max=300.0,
        # 跨进程单飞锁的 TTL，持有者崩溃后由后来者接管
        login_lock_ttl=60.0,
        # 等待其他进程完成登录时的轮询节奏
        poll_interval=0.05,
        poll_max_wait=30.0,
        start_background=True,
        tick_interval=5.0,
    ):
        self.backend = backend or DummyBackend(clock=clock)
        self.store = store if store is not None else InMemoryStore()
        self.clock = clock or Clock()
        self.renew_margin = float(renew_margin)
        self.trial_success_needed = int(trial_success_needed)
        self.account_backoff = float(account_backoff)
        self.account_backoff_max = float(account_backoff_max)
        self.site_backoff = float(site_backoff)
        self.site_backoff_max = float(site_backoff_max)
        self.login_lock_ttl = float(login_lock_ttl)
        self.poll_interval = float(poll_interval)
        self.poll_max_wait = float(poll_max_wait)
        self.tick_interval = float(tick_interval)
        self._owner = "%s-%s" % (uuid.uuid4().hex[:12], id(self))
        self._stop = threading.Event()
        self._thread = None
        if start_background:
            self._start_background()
            atexit.register(self.shutdown)

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #
    def lease(self, account):
        """租一个账号的登录态，返回当前共享令牌及完整状态信息。"""
        with self.store.lock_account(account):
            now = self.clock.time()
            rec = self._get_or_create(account, now)

            # 暂停期间：不发起任何新的登录，只回报恢复时间
            if rec.is_paused(now):
                return self._lease_view(rec, now, paused=True)

            # 暂停刚结束：自动转试探期，调度无需重启即可继续接单
            if rec.state == PAUSED:
                self._enter_trial(rec)

            # 有效令牌，且进入续期窗口：先尝试静默续期
            if rec.needs_renew(now, self.renew_margin):
                rec, _renewed_relogin = self._renew_flow(rec, now)
                if rec.is_paused(now):
                    return self._lease_view(rec, now, paused=True)

            # 没有令牌或已过期（含续期失败、被踢）：单飞重登一次
            if rec.is_expired(now):
                # 此前签发过令牌才是重登；全新账号是首次登录（None）
                is_relogin = True if rec.token_issue_at else None
                rec, _ = self._login_flow(rec, now, relogin=is_relogin)
                if rec.is_paused(now):
                    return self._lease_view(rec, now, paused=True)

            rec.lease_count += 1
            self.store.put(rec)
            return self._lease_view(rec, now, paused=False)

    def refresh(self, token):
        """主动续期一个令牌；被踢或已过期则单飞重登。"""
        account = self.store.lookup_token(token)
        if account is None:
            return {
                "ok": False,
                "token": token,
                "error_kind": "unknown_token",
                "reason": "unknown_token",
                "refreshed": False,
                "relogin": False,
            }

        with self.store.lock_account(account):
            now = self.clock.time()
            rec = self._get_or_create(account, now)

            if rec.is_paused(now):
                return self._refresh_view(rec, now, paused=True)

            if rec.state == PAUSED:
                self._enter_trial(rec)

            did_relogin = False

            # 令牌还在：先静默续期（被踢时内部会单飞重登并告知）
            if rec.token and not rec.is_expired(now):
                rec, did_relogin = self._renew_flow(rec, now)
                if rec.is_paused(now):
                    return self._refresh_view(rec, now, paused=True)

            # 令牌本身已过期：再补一次单飞重登
            if rec.is_expired(now):
                # 此前签发过令牌（含被踢后清空的场景），属于重登
                is_relogin = True if rec.token_issue_at else None
                rec, relogin_happened = self._login_flow(rec, now, relogin=is_relogin)
                did_relogin = did_relogin or relogin_happened
                if rec.is_paused(now):
                    return self._refresh_view(rec, now, paused=True)

            self.store.put(rec)
            return self._refresh_view(rec, now, paused=False, relogin=did_relogin)

    # ------------------------------------------------------------------ #
    # 后台维护：暂停到期自动转试探 + 到期前主动续期
    # ------------------------------------------------------------------ #
    def tick(self):
        """执行一轮后台维护。测试里手动调用，线上由后台线程周期调用。"""
        for account in self._known_accounts():
            try:
                with self.store.lock_account(account):
                    now = self.clock.time()
                    rec = self.store.get(account)
                    if rec is None or rec.is_paused(now):
                        continue
                    entered_trial = False
                    if rec.state == PAUSED:
                        # 暂停到期：自动进入小流量试探，自动接单
                        rec.state = TRIAL
                        rec.consecutive_success = 0
                        entered_trial = True

                    if rec.is_expired(now):
                        # 令牌已过期且没有在途请求：后台主动单飞重登，
                        # 让下一个 lease 直接命中
                        is_relogin = True if rec.token_issue_at else None
                        rec, _ = self._login_flow(rec, now, relogin=is_relogin)
                    elif rec.needs_renew(now, self.renew_margin):
                        rec, _ = self._renew_flow(rec, now)
                    elif entered_trial:
                        # 令牌仍有效：仅落状态转换，成功次数等真实认证再累计
                        pass
                    else:
                        continue
                    self.store.put(rec)
            except Exception:
                # 单账号维护失败不影响其他账号
                continue

    def shutdown(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------ #
    # 内部：会话与状态转换
    # ------------------------------------------------------------------ #
    def _get_or_create(self, account, now):
        rec = self.store.get(account)
        if rec is None:
            rec = SessionRecord(account=account, updated_at=now)
            self.store.put(rec)
        return rec

    def _enter_trial(self, rec):
        """暂停到期：进入小流量试探期。

        这里只做状态转换；本次恢复登录的成功计数由随后的
        ``_apply_result -> _on_success`` 统一完成，避免双重计数。
        """
        rec.state = TRIAL
        rec.consecutive_success = 0

    def _known_accounts(self):
        if isinstance(self.store, InMemoryStore):
            return list(self.store._sessions.keys())
        conn = getattr(self.store, "_conn", None)
        if conn is not None:
            rows = conn.execute("SELECT account FROM sessions").fetchall()
            return [r[0] for r in rows]
        return []

    def _apply_result(self, rec, result, now, relogin):
        """写入一次成功登录/续期的结果。

        ``relogin`` 三态语义：
        - True：被踢/过期后的完整重登，置 ever_relogin；
        - False：静默续期，置 ever_refreshed；
        - None：首次登录，两个履历标记都不动。
        """
        rec.token = result.token
        rec.token_issue_at = now
        rec.token_expires_at = now + result.expires_in
        rec.renewable = result.renewable
        rec.pause_reason = None
        rec.pause_kind = None
        rec.paused_until = 0.0
        # 注意：fail_count 不在此清零。它决定下一次失败的退避档位，
        # 试探期的成功只证明账号在好转，尚未证明稳定；真正恢复
        # HEALTHY 后才在 _on_success 里归零。
        # 成功即解除暂停：转试探期，成功计数统一由 _on_success 累加
        if rec.state == PAUSED:
            rec.state = TRIAL
            rec.consecutive_success = 0
        if relogin is True:
            rec.ever_relogin = True
        elif relogin is False:
            rec.ever_refreshed = True
        rec.updated_at = now
        self._on_success(rec)

    def _on_success(self, rec):
        """成功推进状态机：试探期连续成功达标后恢复正常速度。"""
        if rec.state == TRIAL:
            rec.consecutive_success += 1
            if rec.consecutive_success >= self.trial_success_needed:
                rec.state = HEALTHY
                rec.consecutive_success = 0
                # 连续稳定成功，历史失败计数与退避档位归零
                rec.fail_count = 0
        elif rec.state == HEALTHY:
            rec.consecutive_success = 0

    def _backoff_seconds(self, rec, kind):
        """指数退避：失败次数越多暂停越久。"""
        n = max(0, rec.fail_count - 1)
        if kind == "site":
            base, cap = self.site_backoff, self.site_backoff_max
        else:
            base, cap = self.account_backoff, self.account_backoff_max
        return min(cap, base * (2 ** n))

    def _on_failure(self, rec, exc, now):
        """统一失败处理：分类、计次、按账号暂停。"""
        kind = "site" if isinstance(exc, SiteError) else "account"
        rec.fail_count += 1
        rec.state = PAUSED
        rec.consecutive_success = 0
        rec.pause_kind = kind
        rec.pause_reason = getattr(exc, "reason", type(exc).__name__)
        rec.paused_until = now + self._backoff_seconds(rec, kind)
        rec.updated_at = now

    # ------------------------------------------------------------------ #
    # 内部：静默续期
    # ------------------------------------------------------------------ #
    def _renew_flow(self, rec, now):
        """静默续期，返回 ``(record, relogin)``。

        正常续期 relogin=False；被踢下线回落到单飞重登时 relogin=True；
        其他失败导致暂停时 relogin=False。
        """
        try:
            result = self.backend.renew(rec.account, rec.token)
        except KickedError as exc:
            # 登录态被踢：旧令牌作废并立即持久化，避免重登双检读到旧态
            rec.token = None
            rec.token_expires_at = now
            self.store.put(rec)
            rec2, _ = self._login_flow(
                rec, now, relogin=True, kick_reason=exc.reason
            )
            return rec2, True
        except LoginError as exc:
            self._on_failure(rec, exc, now)
            self.store.put(rec)
            return rec, False
        self._apply_result(rec, result, now, relogin=False)
        self.store.put(rec)
        return rec, False

    # ------------------------------------------------------------------ #
    # 内部：跨进程单飞登录（核心）
    # ------------------------------------------------------------------ #
    def _login_flow(self, rec, now, relogin, kick_reason=None):
        """保证整个集群对同一账号同时只发生一次登录。

        返回 ``(record, did_relogin)``。拿到单飞锁的进程真正调用
        backend.login；其他进程/线程轮询 Store，等会话版本变化后复用
        同一份新登录态——自己这方没有发生登录，``did_relogin`` 为 False。
        """
        account = rec.account
        wait_deadline = now + self.poll_max_wait
        old_token = rec.token

        if self.store.try_acquire_login(account, self._owner, self.login_lock_ttl, now):
            try:
                # 双检：拿锁期间可能别的持有者已完成（锁 TTL 过期接管场景）
                fresh = self.store.get(account)
                if (
                    fresh is not None
                    and not fresh.is_expired(now)
                    and not (kick_reason and fresh.token == old_token)
                ):
                    return fresh, False

                try:
                    result = self.backend.login(account)
                except LoginError as exc:
                    latest = self.store.get(account) or rec
                    self._on_failure(latest, exc, now)
                    self.store.put(latest)
                    return latest, False

                latest = self.store.get(account) or rec
                self._apply_result(latest, result, now, relogin=relogin)
                self.store.put(latest)
                return latest, bool(relogin)
            finally:
                self.store.release_login(account, self._owner)

        # 没拿到锁：等待持有者写回新会话，复用它的登录结果
        last_version = self.store.version_of(account)
        while self.clock.time() < wait_deadline:
            self.clock.sleep(self.poll_interval)
            fresh = self.store.get(account)
            if fresh is None:
                continue
            if self.store.version_of(account) != last_version:
                cur = self.clock.time()
                if fresh.is_paused(cur):
                    return fresh, False
                if not fresh.is_expired(cur):
                    return fresh, False
                last_version = self.store.version_of(account)
        # 等待超时（持有者大概率崩溃，锁 TTL 后下一轮可接管）：按站点失败暂停
        latest = self.store.get(account) or rec
        self._on_failure(latest, SiteError("login_wait_timeout"), now)
        self.store.put(latest)
        return latest, False

    # ------------------------------------------------------------------ #
    # 返回视图
    # ------------------------------------------------------------------ #
    def _lease_view(self, rec, now, paused):
        return {
            "ok": not paused,
            "account": rec.account,
            "state": rec.state,
            "token": None if paused else rec.token,
            "expires_in": 0 if paused else rec.ttl(now),
            "shared": not paused,
            "ever_refreshed": rec.ever_refreshed,
            "ever_relogin": rec.ever_relogin,
            "paused_until": rec.paused_until if paused else 0.0,
            "retry_after": max(0.0, rec.paused_until - now) if paused else 0.0,
            "pause_reason": rec.pause_reason if paused else None,
            "error_kind": rec.pause_kind if paused else None,
            "rate": self._rate(rec),
            "fail_count": rec.fail_count,
        }

    def _refresh_view(self, rec, now, paused, relogin=False):
        return {
            "ok": not paused,
            "account": rec.account,
            "state": rec.state,
            "token": None if paused else rec.token,
            "expires_in": 0 if paused else rec.ttl(now),
            "refreshed": not paused and not relogin,
            "relogin": bool(relogin),
            "ever_refreshed": rec.ever_refreshed,
            "ever_relogin": rec.ever_relogin,
            "paused_until": rec.paused_until if paused else 0.0,
            "retry_after": max(0.0, rec.paused_until - now) if paused else 0.0,
            "pause_reason": rec.pause_reason if paused else None,
            "error_kind": rec.pause_kind if paused else None,
            "rate": self._rate(rec),
            "fail_count": rec.fail_count,
        }

    @staticmethod
    def _rate(rec):
        """当前账号允许的采集速率档位：试探期限流，正常期满速，暂停期为 0。"""
        if rec.state == PAUSED:
            return "paused"
        if rec.state == TRIAL:
            return "trial"
        return "full"

    # ------------------------------------------------------------------ #
    # 后台线程
    # ------------------------------------------------------------------ #
    def _start_background(self):
        def loop():
            while not self._stop.wait(self.tick_interval):
                try:
                    self.tick()
                except Exception:
                    continue

        self._thread = threading.Thread(
            target=loop, name="loginkeep-maintain", daemon=True
        )
        self._thread.start()


# ---------------------------------------------------------------------- #
# 模块级默认实例与对外函数
# ---------------------------------------------------------------------- #
_default_lock = threading.Lock()
_default_manager = None


def get_manager():
    """返回进程内默认管理器（首次使用时惰性创建）。"""
    global _default_manager
    with _default_lock:
        if _default_manager is None:
            _default_manager = LoginManager()
        return _default_manager


def configure(backend=None, store=None, **kwargs):
    """替换默认管理器。多机部署时传入共享的 SqliteStore。

    必须在首次 lease/refresh 之前调用；重复调用会关停旧实例。
    """
    global _default_manager
    with _default_lock:
        if _default_manager is not None:
            _default_manager.shutdown()
        _default_manager = LoginManager(backend=backend, store=store, **kwargs)
        return _default_manager


def reset_default_manager():
    """测试辅助：清掉默认管理器。"""
    global _default_manager
    with _default_lock:
        if _default_manager is not None:
            _default_manager.shutdown()
        _default_manager = None


def lease(account):
    """账号级登录态租约，见 :meth:`LoginManager.lease`。"""
    return get_manager().lease(account)


def refresh(token):
    """令牌续期/重登，见 :meth:`LoginManager.refresh`。"""
    return get_manager().refresh(token)
