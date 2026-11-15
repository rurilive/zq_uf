"""账号级登录态池。

- 同账号共享一份登录态，lease 返回同一个 token
- 过期前由后台线程主动续期
- 被踢下线后单飞重登：并发调用只触发一次真实登录
- 登录失败按账号暂停，到期自动进入小流量试探，连续成功自动恢复
"""

import threading
import time

# 登录失败分类：账号问题还是站点问题
ACCOUNT = "account"  # 密码错、被封、欠费
SITE = "site"        # 超时、5xx、验证码升级


class LoginError(Exception):
    """登录失败。kind 为 ACCOUNT 或 SITE。"""

    def __init__(self, kind, message):
        super().__init__("%s: %s" % (kind, message))
        self.kind = kind
        self.message = message


_demo_seq = [0]


def _demo_authenticator(account):
    """演示用登录器：永远成功，令牌 30 分钟有效。"""
    _demo_seq[0] += 1
    return {"token": "tok-%s-%d-%d" % (account, int(time.time()), _demo_seq[0]),
            "ttl": 1800}


class _Flight:
    """一次进行中的登录，并发调用方等待同一个结果。"""

    def __init__(self):
        self.done = threading.Event()


def _new_state():
    return {
        "token": None,
        "expires_at": 0.0,
        "refreshed": False,    # 当前令牌是否续期得来
        "relogin": False,      # 当前令牌是否踢下线后重登得来
        "paused_until": 0.0,   # 暂停到什么时候（clock 时间），0 表示未暂停
        "fail_count": 0,
        "probing": False,      # 小流量恢复期
        "probe_ok": 0,
        "last_error": None,
        "flight": None,
    }


class LoginPool:
    """登录态池。

    authenticator(account) -> {"token": str, "ttl": 秒}，失败抛 LoginError。
    多机器部署时把同一个池放在登录态服务（或共享存储）后面，
    各机器只调 lease/refresh，即可拿到同一份登录态。
    """

    def __init__(self, authenticator=None, *, refresh_margin=60.0,
                 pause_seconds=60.0, pause_max=1800.0,
                 site_pause_seconds=10.0, probe_successes=2,
                 clock=time.time):
        self._auth = authenticator or _demo_authenticator
        self._margin = refresh_margin        # 剩余有效期低于该值即续期
        self._pause = pause_seconds          # 账号问题的基础暂停时长
        self._pause_max = pause_max          # 连续失败指数退避上限
        self._site_pause = site_pause_seconds  # 站点问题的暂停时长（较短）
        self._probe_needed = probe_successes  # 恢复期要求的连续成功次数
        self._clock = clock
        self._mu = threading.Lock()
        self._accounts = {}      # account -> state dict
        self._locks = {}         # account -> threading.Lock
        self._token_index = {}   # token（含历史）-> account
        self._sweeper = None
        self._sweeper_stop = threading.Event()

    # ------------------------------------------------------------------ API

    def lease(self, account):
        """取登录态：同账号共享；过期/被踢自动重登；暂停期直接返回暂停信息。"""
        self._ensure(account)
        did_login = False
        while True:
            with self._locks[account]:
                st = self._accounts[account]
                now = self._clock()
                if st["token"] and st["expires_at"] > now:
                    if not did_login:  # 本次调用刚登录过，不重复计试探成功
                        self._probe_success(st)
                    return self._response(account, st)
                if st["paused_until"] > now:
                    # 暂停期间不再发起新的登录
                    return self._response(account, st, error=st["last_error"])
                self._maybe_unpause(st, now)
                reason = "relogin" if st["token"] else "login"
                flight, leader = self._flight_locked(st)
            if leader:
                self._run_login(account, flight, reason)
                did_login = True
            else:
                flight.done.wait()

    def refresh(self, token):
        """续期。临近过期才真正登录；已被踢（失效）则单飞重登一次。

        其他机器若已续期/重登，这里直接返回当前共享令牌。
        """
        account = self._token_account(token)
        if account is None:
            return {"account": None, "token": None, "expires_in": 0,
                    "shared": True, "refreshed": False, "relogin": False,
                    "probing": False, "paused_until": None,
                    "error": {"kind": "unknown_token",
                              "message": "token 不在池中，请重新 lease"}}
        self._ensure(account)
        while True:
            with self._locks[account]:
                st = self._accounts[account]
                now = self._clock()
                if st["token"] != token:
                    # 别的机器已续期/重登，返回当前共享令牌
                    return self._response(account, st)
                if st["token"] and st["expires_at"] - now > self._margin:
                    return self._response(account, st)  # 还早，无需续期
                if st["paused_until"] > now:
                    return self._response(account, st, error=st["last_error"])
                self._maybe_unpause(st, now)
                reason = "refresh" if st["expires_at"] > now else "relogin"
                flight, leader = self._flight_locked(st)
            if leader:
                self._run_login(account, flight, reason)
            else:
                flight.done.wait()

    def invalidate(self, token):
        """模拟被站点踢下线：令牌立即失效，下次 lease/refresh 触发重登。"""
        account = self._token_account(token)
        if account is None:
            return False
        with self._locks[account]:
            st = self._accounts[account]
            if st["token"] != token:
                return False
            st["expires_at"] = 0.0
        return True

    def start_background_refresh(self, interval=5.0):
        """后台线程：令牌剩余有效期进入 margin 时提前续期。"""
        with self._mu:
            if self._sweeper is not None:
                return self._sweeper
            self._sweeper_stop.clear()
            t = threading.Thread(target=self._sweep_loop, args=(interval,),
                                 name="loginkeep-refresher", daemon=True)
            self._sweeper = t
            t.start()
            return t

    def stop_background_refresh(self):
        self._sweeper_stop.set()
        with self._mu:
            t = self._sweeper
            self._sweeper = None
        if t is not None:
            t.join(timeout=2.0)

    # -------------------------------------------------------------- 内部

    def _ensure(self, account):
        with self._mu:
            if account not in self._accounts:
                self._accounts[account] = _new_state()
                self._locks[account] = threading.Lock()

    def _token_account(self, token):
        with self._mu:
            return self._token_index.get(token)

    def _flight_locked(self, st):
        """在账号锁内调用：要么成为本次登录的领导者，要么拿到进行中的 flight。"""
        if st["flight"] is None:
            st["flight"] = _Flight()
            return st["flight"], True
        return st["flight"], False

    def _run_login(self, account, flight, reason):
        try:
            cred = self._auth(account)
            self._commit_login(account, cred, reason)
        except LoginError as e:
            self._commit_failure(account, e)
        except Exception as e:  # 未分类异常按站点问题处理
            self._commit_failure(account, LoginError(SITE, str(e)))
        finally:
            with self._locks[account]:
                if self._accounts[account]["flight"] is flight:
                    self._accounts[account]["flight"] = None
            flight.done.set()

    def _login_singleflight(self, account, reason):
        with self._locks[account]:
            flight, leader = self._flight_locked(self._accounts[account])
        if leader:
            self._run_login(account, flight, reason)
        else:
            flight.done.wait()

    def _commit_login(self, account, cred, reason):
        now = self._clock()
        with self._locks[account]:
            st = self._accounts[account]
            st["token"] = cred["token"]
            st["expires_at"] = now + float(cred["ttl"])
            st["refreshed"] = reason == "refresh"
            st["relogin"] = reason == "relogin"
            st["last_error"] = None
            self._probe_success(st)
            if not st["probing"]:
                st["fail_count"] = 0
            with self._mu:
                # 旧 token 映射保留：其他机器拿旧 token refresh 时
                # 仍能找到账号，拿到当前共享令牌
                self._token_index[cred["token"]] = account

    def _commit_failure(self, account, err):
        now = self._clock()
        with self._locks[account]:
            st = self._accounts[account]
            st["fail_count"] += 1
            st["last_error"] = {"kind": err.kind, "message": err.message}
            if err.kind == ACCOUNT:
                pause = min(self._pause * (2 ** (st["fail_count"] - 1)),
                            self._pause_max)
            else:
                pause = self._site_pause
            st["paused_until"] = now + pause
            st["probing"] = False
            st["probe_ok"] = 0

    def _maybe_unpause(self, st, now):
        """暂停到期自动进入小流量试探，无需人工重启。"""
        if (st["paused_until"] > 0 and st["paused_until"] <= now
                and st["fail_count"] > 0 and not st["probing"]):
            st["probing"] = True
            st["probe_ok"] = 0

    def _probe_success(self, st):
        if not st["probing"]:
            return
        st["probe_ok"] += 1
        if st["probe_ok"] >= self._probe_needed:
            st["probing"] = False
            st["probe_ok"] = 0
            st["fail_count"] = 0

    def _response(self, account, st, error=None):
        now = self._clock()
        usable = st["token"] if st["token"] and st["expires_at"] > now else None
        return {
            "account": account,
            "token": usable,
            "expires_in": int(st["expires_at"] - now) if usable else 0,
            "shared": True,
            "refreshed": st["refreshed"],
            "relogin": st["relogin"],
            "probing": st["probing"],
            "paused_until": st["paused_until"] if st["paused_until"] > now else None,
            "error": error,
        }

    def _sweep(self):
        with self._mu:
            accounts = list(self._accounts)
        now = self._clock()
        for account in accounts:
            with self._locks[account]:
                st = self._accounts[account]
                need = (st["token"] and st["flight"] is None
                        and st["fail_count"] == 0  # 暂停/恢复期不主动登录
                        and 0 < st["expires_at"] - now <= self._margin)
            if need:
                self._login_singleflight(account, "refresh")

    def _sweep_loop(self, interval):
        while not self._sweeper_stop.wait(interval):
            self._sweep()


_default_pool = LoginPool()


def lease(account):
    return _default_pool.lease(account)


def refresh(token):
    return _default_pool.refresh(token)
