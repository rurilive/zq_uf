import threading
import time
import unittest

from siteprobe import Samples, probe

BASE = "http://conc.test"


class ConcurrencyTest(unittest.TestCase):
    def test_pages_fetched_concurrently_with_rate_limit(self):
        active = {"current": 0, "max": 0}
        lock = threading.Lock()

        def fetcher(url, timeout=None):
            with lock:
                active["current"] += 1
                active["max"] = max(active["max"], active["current"])
            time.sleep(0.05)
            with lock:
                active["current"] -= 1
            body = b"<html><body>ok</body></html>"
            return {"status": 200, "body": body,
                    "headers": {"content-type": "text/html; charset=utf-8"},
                    "url": url, "redirects": [], "elapsed": 0.05}

        list_urls = [BASE + "/list%d" % index for index in range(4)]
        start = time.monotonic()
        report = probe(BASE + "/", Samples(
            list_urls=list_urls,
            detail_count=0,
            max_workers=4,
            rate_per_sec=20,
            fetcher=fetcher,
        ))
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(active["max"], 2)
        self.assertEqual(len(report["pages"]), 5)
        self.assertEqual(report["rate_limit"]["requests"], 5)
        self.assertGreaterEqual(elapsed, 4.0 / 20 - 0.02)

    def test_fetcher_exception_does_not_abort_probe(self):
        def fetcher(url, timeout=None):
            if url.endswith("/list1"):
                raise RuntimeError("boom")
            return {"status": 200, "body": b"<html>ok</html>",
                    "headers": {"content-type": "text/html"},
                    "url": url, "redirects": [], "elapsed": 0.0}

        report = probe(BASE + "/", Samples(
            list_urls=[BASE + "/list0", BASE + "/list1"],
            detail_count=0,
            fetcher=fetcher,
            rate_per_sec=0,
        ))
        failed = next(page for page in report["pages"] if page["url"].endswith("/list1"))
        self.assertEqual(failed["status"], 0)
        self.assertIn("fetcher_error", failed["error"])
        self.assertEqual(len(report["pages"]), 3)


if __name__ == "__main__":
    unittest.main()
