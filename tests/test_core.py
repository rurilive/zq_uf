import unittest

from linkguard import follow


class LinkguardTest(unittest.TestCase):
    def test_returns_response(self):
        self.assertEqual(follow("http://s/a", lambda url: {"status": 200}), {"status": 200})

    def test_passes_url(self):
        seen = {}
        follow("http://s/a", lambda url: seen.setdefault("url", url))
        self.assertEqual(seen["url"], "http://s/a")

    def test_follows_chain_and_reports(self):
        routes = {
            "http://s/a": {"status": 302, "location": "/b"},
            "http://s/b": {"status": 200, "body": "done"},
        }
        report = follow("http://s/a", lambda url: routes[url])
        self.assertEqual(report["final_url"], "http://s/b")
        self.assertEqual(report["redirects"], 1)
        self.assertFalse(report["cycle"])
        self.assertIsNone(report["error"])
        self.assertEqual(report["chain"], ["http://s/a", "http://s/b"])
        self.assertEqual(report["response"], {"status": 200, "body": "done"})

    def test_detects_cycle(self):
        routes = {
            "http://s/a": {"status": 302, "location": "/b"},
            "http://s/b": {"status": 302, "location": "/a"},
        }
        report = follow("http://s/a", lambda url: routes[url])
        self.assertTrue(report["cycle"])
        self.assertEqual(report["error"], "redirect cycle detected")
        self.assertEqual(report["chain"], ["http://s/a", "http://s/b", "http://s/a"])

    def test_session_params_merged(self):
        # 同一页带不同会话参数，归并后判为同一目标，立即成环
        routes = {
            "http://s/home": {"status": 302, "location": "/home?sid=aaa"},
            "http://s/home?sid=aaa": {"status": 302, "location": "/home?sid=bbb"},
        }
        fetched = []

        def fetch(url):
            fetched.append(url)
            return routes[url]

        report = follow("http://s/home", fetch)
        self.assertTrue(report["cycle"])
        self.assertEqual(fetched, ["http://s/home"])
        self.assertEqual(report["chain"], ["http://s/home", "http://s/home?sid=aaa"])

    def test_cross_domain_recorded_not_followed(self):
        fetched = []

        def fetch(url):
            fetched.append(url)
            return {"status": 302, "location": "http://other.example.com/clone"}

        report = follow("http://s/page", fetch)
        self.assertEqual(fetched, ["http://s/page"])
        self.assertEqual(report["error"], "cross-domain redirect not followed")
        self.assertEqual(
            report["cross_domain"],
            [{"from": "http://s/page", "to": "http://other.example.com/clone"}],
        )
        self.assertEqual(report["final_url"], "http://s/page")

    def test_cross_domain_opt_in(self):
        routes = {
            "http://s/page": {"status": 302, "location": "http://cdn.example.com/x"},
            "http://cdn.example.com/x": {"status": 200},
        }
        report = follow("http://s/page", lambda url: routes[url],
                        follow_cross_domain=True)
        self.assertEqual(report["final_url"], "http://cdn.example.com/x")
        self.assertEqual(len(report["cross_domain"]), 1)
        self.assertIsNone(report["error"])

    def test_max_redirects(self):
        def fetch(url):
            n = int(url.rsplit("/", 1)[1])
            return {"status": 302, "location": "/%d" % (n + 1)}

        report = follow("http://s/0", fetch, max_redirects=3)
        self.assertEqual(report["redirects"], 3)
        self.assertFalse(report["cycle"])
        self.assertEqual(report["error"], "max redirects exceeded (3)")
        self.assertEqual(report["final_url"], "http://s/3")
