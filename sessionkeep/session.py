"""登录态保持：UTC 过期、提前刷新、单线程刷新、登录页识别与重放。"""

import datetime as _dt
import threading

_DEFAULT_LOGIN_MARKERS = (
    'name="password"',
    "name='password'",
    'type="password"',
    'id="login-form"',
    'action="/login"',
)


class SessionError(Exception):
    """sessionkeep 所有异常的基类。"""


class SessionRefreshError(SessionError):
    """连续刷新登录态失败。"""


class LoginPageDetected(SessionError):
    """重登并重放后仍然拿到登录页。"""


class EmptyResponse(SessionError):
    """重登并重放后仍然拿到空响应。"""


def _to_seconds(value):
    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    return float(value)


class Session:
    """线程安全的登录态保持器，仅依赖 Python 标准库。

    所有过期时间统一使用带时区的 UTC ``datetime``，不受机器时区影响。
    时间来源 ``time_func`` 可注入，便于测试与演示时间推进。
    """

    def __init__(
        self,
        login,
        fetch_page,
        ttl=None,
        refresh_margin=300,
        login_markers=None,
        max_refresh_failures=3,
        time_func=None,
    ):
        self.login = login
        self.fetch_page = fetch_page
        self.ttl = None if ttl is None else _to_seconds(ttl)
        self.refresh_margin = _to_seconds(refresh_margin)
        self.login_markers = tuple(
            login_markers if login_markers is not None else _DEFAULT_LOGIN_MARKERS
        )
        self.max_refresh_failures = max_refresh_failures
        self.time_func = time_func or (lambda: _dt.datetime.now(_dt.timezone.utc))

        self.cookie = None
        self.expires_at = None
        self.login_count = 0
        self.refresh_failures = 0
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._generation = 0

    def ensure_session(self, force=False):
        """返回可用 cookie；过期或 ``force`` 时刷新。

        同一时刻只允许一个线程刷新，其余线程等待并复用新 cookie。
        """
        with self._condition:
            return self._ensure_after(self._generation, force)

    def _ensure_after(self, generation, force):
        with self._condition:
            if not force and self.cookie is not None and not self._is_stale():
                return self.cookie

            if generation == self._generation:
                self._generation += 1
                self._condition.notify_all()
                self._refresh_locked()
                generation = self._generation
            else:
                while self._generation < generation:
                    self._condition.wait()
            return self.cookie

    def _refresh_locked(self):
        try:
            result = self.login()
        except Exception as exc:
            self.refresh_failures += 1
            if self.refresh_failures >= self.max_refresh_failures:
                raise SessionRefreshError(
                    "连续 %d 次刷新登录态失败: %s"
                    % (self.refresh_failures, exc)
                ) from exc
            self.cookie = None
            self.expires_at = None
            return

        cookie, expires_at = self._parse_login_result(result)
        self.cookie = cookie
        self.expires_at = expires_at
        self.login_count += 1
        self.refresh_failures = 0

    def _parse_login_result(self, result):
        if isinstance(result, tuple):
            cookie, expiry = result
        else:
            cookie, expiry = result, None

        if self.ttl is not None:
            expires_at = self.time_func() + _dt.timedelta(seconds=self.ttl)
        elif isinstance(expiry, _dt.datetime):
            expires_at = self._as_utc(expiry)
        elif expiry is not None:
            expires_at = _dt.datetime.fromtimestamp(
                float(expiry), tz=_dt.timezone.utc
            )
        else:
            expires_at = None
        return cookie, expires_at

    @staticmethod
    def _as_utc(value):
        if value.tzinfo is None:
            return value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc)

    def _is_stale(self):
        if self.cookie is None:
            return True
        if self.expires_at is None:
            return False
        now = self.time_func()
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
        return now + _dt.timedelta(seconds=self.refresh_margin) >= self.expires_at

    def _classify_response(self, response):
        if response is None:
            return "empty"
        if isinstance(response, bytes):
            text = response.decode("utf-8", "ignore")
        else:
            text = str(response)
        if not text.strip():
            return "empty"
        lowered = text.lower()
        if any(marker.lower() in lowered for marker in self.login_markers):
            return "login"
        return None

    def fetch(self, url):
        """带登录态取数；命中登录页或空响应则重登并重放当前请求一次。"""
        generation = self._generation
        last_kind = None
        for _ in range(2):
            cookie = self._ensure_after(generation, force=last_kind is not None)
            response = self.fetch_page(url, cookie)
            kind = self._classify_response(response)
            if kind is None:
                return response
            last_kind = kind
            generation = self._generation

        if last_kind == "login":
            raise LoginPageDetected("重登并重放后仍返回登录页: %s" % url)
        raise EmptyResponse("重登并重放后仍返回空响应: %s" % url)
