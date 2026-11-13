import unittest

from tlssafe import FetchError, fetch, set_opener


class TlsTest(unittest.TestCase):
    def test_success(self):
        set_opener(lambda url: (200, "ok"))
        self.assertEqual(fetch("https://shop/1"), (200, "ok"))

    def test_cert_error_becomes_generic(self):
        def opener(url):
            raise ValueError("certificate has expired")

        set_opener(opener)
        with self.assertRaises(FetchError) as ctx:
            fetch("https://shop/1")
        self.assertIn("certificate", ctx.exception.reason)
        self.assertNotIn("kind", dir(ctx.exception))

    def test_retries_on_any_error(self):
        calls = []

        def opener(url):
            calls.append(url)
            raise ValueError("hostname mismatch")

        set_opener(opener)
        with self.assertRaises(FetchError):
            fetch("https://shop/1", retries=2)
        self.assertEqual(len(calls), 3)
