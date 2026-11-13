import http.server
import threading
import time
import unittest

from detailcrawl import crawl
from detailcrawl.fetcher import ConnectionPool, HttpFetcher

OK_BODY = (
    b'<html><head><title>t</title></head>'
    b'<body><span data-field="price">1</span></body></html>'
)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _respond(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        self.server.connections.add(id(self.connection))
        if self.path.startswith("/ok"):
            self._respond(200, OK_BODY)
        elif self.path.startswith("/missing"):
            self._respond(404)
        elif self.path.startswith("/boom"):
            self._respond(500)
        elif self.path.startswith("/flaky"):
            hits = self.server.hits.get(self.path, 0) + 1
            self.server.hits[self.path] = hits
            self._respond(500 if hits < 3 else 200, OK_BODY if hits >= 3 else b"")
        elif self.path.startswith("/slow"):
            time.sleep(2)
            self._respond(200, OK_BODY)
        else:
            self._respond(404)


class QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class FetcherTestBase(unittest.TestCase):
    def setUp(self):
        self.server = QuietServer(("127.0.0.1", 0), Handler)
        self.server.connections = set()
        self.server.hits = {}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()


class ConnectionReuseTest(FetcherTestBase):
    def test_sequential_requests_reuse_one_connection(self):
        pool = ConnectionPool(timeout=5)
        fetcher = HttpFetcher(pool=pool)
        for i in range(5):
            self.assertIn("price", fetcher.fetch(f"{self.base}/ok/{i}"))
        self.assertEqual(pool.opened_count, 1)
        self.assertEqual(len(self.server.connections), 1)
        pool.close()

    def test_crawl_reuses_connections_and_limits_per_host(self):
        result = crawl([f"{self.base}/ok/{i}" for i in range(12)], max_per_host=3)
        self.assertEqual(len(result.stats.succeeded), 12)
        self.assertLessEqual(len(self.server.connections), 3)


class ErrorClassificationTest(FetcherTestBase):
    def test_404_skipped_without_retry(self):
        result = crawl([f"{self.base}/missing"], retries=2, backoff=0)
        self.assertEqual(len(result.stats.skipped), 1)
        self.assertEqual(result.stats.skipped[0].attempts, 1)
        self.assertIn("404", result.stats.skipped[0].detail)

    def test_5xx_retried_then_failed(self):
        result = crawl([f"{self.base}/boom"], retries=2, backoff=0)
        self.assertEqual(len(result.stats.failed), 1)
        self.assertEqual(result.stats.failed[0].attempts, 3)
        self.assertIn("500", result.stats.failed[0].detail)

    def test_flaky_server_recovers_within_retries(self):
        result = crawl([f"{self.base}/flaky"], retries=2, backoff=0)
        self.assertEqual(len(result.stats.succeeded), 1)
        self.assertEqual(result.stats.succeeded[0].attempts, 3)

    def test_timeout_retried_then_failed(self):
        result = crawl([f"{self.base}/slow"], timeout=0.3, retries=1, backoff=0)
        self.assertEqual(len(result.stats.failed), 1)
        record = result.stats.failed[0]
        self.assertEqual(record.attempts, 2)
        self.assertIn("timed out", record.detail)


if __name__ == "__main__":
    unittest.main()
