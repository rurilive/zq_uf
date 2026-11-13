import http.client
import socket
import ssl
import unittest
import urllib.error

from retryall import ErrorClass, HttpError, PermanentError, call, classify


class ClassificationTest(unittest.TestCase):
    def test_timeout_errors(self):
        self.assertEqual(classify(TimeoutError("timed out")), ErrorClass.TIMEOUT)
        self.assertEqual(classify(socket.timeout("timed out")), ErrorClass.TIMEOUT)
        wrapped = urllib.error.URLError(socket.timeout("timed out"))
        self.assertEqual(classify(wrapped), ErrorClass.TIMEOUT)

    def test_connection_errors(self):
        self.assertEqual(
            classify(ConnectionResetError("connection reset")),
            ErrorClass.CONNECTION,
        )
        self.assertEqual(
            classify(http.client.RemoteDisconnected("closed")),
            ErrorClass.CONNECTION,
        )
        self.assertEqual(classify(ssl.SSLError("reset")), ErrorClass.CONNECTION)

    def test_rate_limit_server_and_permanent_http(self):
        self.assertEqual(
            classify(HttpError(429)), ErrorClass.RATE_LIMIT
        )
        self.assertEqual(classify(HttpError(503)), ErrorClass.SERVER)
        self.assertEqual(classify(HttpError(500)), ErrorClass.SERVER)
        self.assertEqual(classify(HttpError(404)), ErrorClass.PERMANENT)
        self.assertEqual(classify(HttpError(400)), ErrorClass.PERMANENT)

    def test_urllib_http_error_statuses(self):
        def urllib_error(status):
            try:
                raise urllib.error.HTTPError(
                    "http://x", status, "err", {}, None
                )
            except urllib.error.HTTPError as exc:
                return exc

        self.assertEqual(classify(urllib_error(404)), ErrorClass.PERMANENT)
        self.assertEqual(classify(urllib_error(429)), ErrorClass.RATE_LIMIT)
        self.assertEqual(classify(urllib_error(502)), ErrorClass.SERVER)

    def test_explicit_permanent_error(self):
        self.assertEqual(
            classify(PermanentError("no such thing")), ErrorClass.PERMANENT
        )

    def test_unknown_exception_is_transient_fallback(self):
        self.assertEqual(classify(ValueError("boom")), ErrorClass.TRANSIENT)

    def test_permanent_error_not_retried(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise HttpError(404)

        with self.assertRaises(HttpError):
            call(boom)
        self.assertEqual(calls["n"], 1)

    def test_rate_limit_retried_by_its_own_limit(self):
        calls = {"n": 0}

        def throttled():
            calls["n"] += 1
            raise HttpError(429)

        with self.assertRaises(HttpError):
            call(throttled, {"base_delay": 0})
        self.assertEqual(calls["n"], 5)
