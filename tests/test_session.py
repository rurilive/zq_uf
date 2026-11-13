import datetime as dt
import threading
import unittest

from sessionkeep import (
    EmptyResponse,
    LoginPageDetected,
    Session,
    SessionRefreshError,
)


class FakeClock:
    def __init__(self, start=None):
        self.now = start or dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += dt.timedelta(seconds=seconds)


LOGIN_PAGE = '<html><form><input type="password" name="password"></form></html>'


class SessionkeepTest(unittest.TestCase):
    def test_login_only_once(self):
        calls = []

        def login():
            calls.append("login")
            return "cookie-1"

        session = Session(login, lambda url, cookie: "ok")
        session.ensure_session()
        session.ensure_session()
        self.assertEqual(calls, ["login"])
        self.assertEqual(session.login_count, 1)

    def test_fetch_passes_cookie(self):
        seen = {}

        def fetch_page(url, cookie):
            seen["cookie"] = cookie
            return "payload"

        session = Session(lambda: "cookie-2", fetch_page)
        self.assertEqual(session.fetch("http://site/a"), "payload")
        self.assertEqual(seen["cookie"], "cookie-2")

    def test_login_on_first_fetch(self):
        session = Session(lambda: "cookie-3", lambda url, cookie: "ok")
        self.assertIsNone(session.cookie)
        session.fetch("http://site/a")
        self.assertEqual(session.cookie, "cookie-3")

    def test_refresh_before_expiry(self):
        clock = FakeClock()
        cookies = iter(["cookie-a", "cookie-b"])
        logins = []

        def login():
            cookie = next(cookies)
            logins.append(cookie)
            return cookie, clock() + dt.timedelta(hours=1)

        session = Session(login, lambda url, cookie: "ok",
                          refresh_margin=300, time_func=clock)
        session.ensure_session()
        clock.advance(54 * 60)
        self.assertEqual(session.ensure_session(), "cookie-a")
        clock.advance(7 * 60)
        self.assertEqual(session.ensure_session(), "cookie-b")
        self.assertEqual(logins, ["cookie-a", "cookie-b"])

    def test_expiry_uses_utc_ignoring_local_timezone(self):
        clock = FakeClock()
        fixed_expiry = clock() + dt.timedelta(hours=1)
        logins = []

        def login():
            logins.append(1)
            return "cookie-utc", fixed_expiry

        session = Session(
            login,
            lambda url, cookie: "ok",
            refresh_margin=0,
            time_func=clock,
        )
        with patch_timezone("Asia/Kolkata"):
            session.ensure_session()
            clock.advance(59 * 60 + 59)
            self.assertEqual(session.ensure_session(), "cookie-utc")
            self.assertEqual(logins, [1])
            clock.advance(1)
            session.ensure_session()
            self.assertEqual(logins, [1, 1])

    def test_naive_datetime_and_epoch_seconds_are_treated_as_utc(self):
        clock = FakeClock()
        naive_expiry = dt.datetime(2026, 1, 1, 1, 0, 0)
        epoch_expiry = (clock() + dt.timedelta(hours=1)).timestamp()
        session_naive = Session(
            lambda: ("cookie", naive_expiry),
            lambda url, cookie: "ok",
            refresh_margin=0,
            time_func=clock,
        )
        session_naive.ensure_session()
        self.assertEqual(
            session_naive.expires_at,
            dt.datetime(2026, 1, 1, 1, 0, 0, tzinfo=dt.timezone.utc),
        )
        session_epoch = Session(
            lambda: ("cookie", epoch_expiry),
            lambda url, cookie: "ok",
            refresh_margin=0,
            time_func=clock,
        )
        session_epoch.ensure_session()
        self.assertEqual(
            session_epoch.expires_at, clock() + dt.timedelta(hours=1)
        )

    def test_concurrent_refresh_logs_in_only_once(self):
        clock = FakeClock()
        barrier = threading.Barrier(8)
        logins = []

        def login():
            threading.Event().wait(0.05)
            logins.append(1)
            return "cookie-shared", clock() + dt.timedelta(hours=1)

        session = Session(login, lambda url, cookie: "ok", time_func=clock)
        cookies = []

        def worker():
            barrier.wait()
            cookies.append(session.ensure_session())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(logins), 1)
        self.assertEqual(set(cookies), {"cookie-shared"})

    def test_concurrent_refresh_after_expiry_logs_in_only_once(self):
        clock = FakeClock()
        logins = []
        barrier = threading.Barrier(6)

        def login():
            threading.Event().wait(0.05)
            logins.append(1)
            return "cookie-new", clock() + dt.timedelta(hours=1)

        session = Session(login, lambda url, cookie: "ok",
                          refresh_margin=0, time_func=clock)
        session.ensure_session()
        clock.advance(3601)
        cookies = []

        def worker():
            barrier.wait()
            cookies.append(session.ensure_session())

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(logins, [1, 1])
        self.assertEqual(set(cookies), {"cookie-new"})

    def test_login_page_triggers_relogin_and_replay_once(self):
        cookies_seen = []
        logins = []

        def login():
            cookie = "cookie-%d" % (len(logins) + 1)
            logins.append(cookie)
            return cookie

        def fetch_page(url, cookie):
            cookies_seen.append(cookie)
            if cookie == "cookie-1":
                return LOGIN_PAGE
            return "payload"

        session = Session(login, fetch_page)
        self.assertEqual(session.fetch("http://site/list"), "payload")
        self.assertEqual(logins, ["cookie-1", "cookie-2"])
        self.assertEqual(cookies_seen, ["cookie-1", "cookie-2"])

    def test_login_page_twice_raises_login_page_detected(self):
        session = Session(lambda: "cookie", lambda url, cookie: LOGIN_PAGE)
        with self.assertRaises(LoginPageDetected):
            session.fetch("http://site/list")
        self.assertEqual(session.login_count, 2)

    def test_empty_response_triggers_relogin_and_raises_when_persistent(self):
        session = Session(lambda: "cookie", lambda url, cookie: "  \n ")
        with self.assertRaises(EmptyResponse):
            session.fetch("http://site/list")
        self.assertEqual(session.login_count, 2)

    def test_custom_login_marker(self):
        page = "<html><body>请重新登录后再访问</body></html>"
        session = Session(
            lambda: "cookie",
            lambda url, cookie: page,
            login_markers=["请重新登录"],
        )
        with self.assertRaises(LoginPageDetected):
            session.fetch("http://site/list")

    def test_consecutive_refresh_failures_raise_clear_error(self):
        attempts = []

        def login():
            attempts.append(1)
            raise RuntimeError("认证服务不可用")

        session = Session(login, lambda url, cookie: "ok",
                          max_refresh_failures=3)
        with self.assertRaises(SessionRefreshError) as ctx:
            for _ in range(3):
                session.ensure_session()
        self.assertEqual(len(attempts), 3)
        self.assertIn("连续 3 次刷新", str(ctx.exception))

    def test_successful_login_resets_failure_count(self):
        clock = FakeClock()
        failures = iter([RuntimeError("boom"), None])

        def login():
            failure = next(failures)
            if failure is not None:
                raise failure
            return "cookie-ok", clock() + dt.timedelta(hours=1)

        session = Session(login, lambda url, cookie: "ok",
                          max_refresh_failures=2, time_func=clock)
        session.ensure_session()
        self.assertEqual(session.refresh_failures, 1)
        session.ensure_session(force=True)
        self.assertEqual(session.refresh_failures, 0)
        self.assertEqual(session.cookie, "cookie-ok")


class patch_timezone:
    def __init__(self, tzname):
        self.tzname = tzname
        self.old = None

    def __enter__(self):
        import os
        import time as time_module
        self.old = os.environ.get("TZ")
        os.environ["TZ"] = self.tzname
        if hasattr(time_module, "tzset"):
            time_module.tzset()

    def __exit__(self, *exc):
        import os
        import time as time_module
        if self.old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old
        if hasattr(time_module, "tzset"):
            time_module.tzset()
