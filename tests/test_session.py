import unittest

from sessionkeep import Session


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
