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


import json
import os
import ssl
import tempfile

from tlssafe import (
    CERT_EXPIRED,
    CERT_HOSTNAME_MISMATCH,
    CERT_INCOMPLETE_CHAIN,
    CertError,
    allow_host,
    clear_allowlist,
    set_audit_log,
    set_unverified_opener,
)


def _cert_exc(verify_code, message):
    exc = ssl.SSLCertVerificationError(message)
    exc.verify_code = verify_code
    exc.verify_message = message
    exc.cert_subject = "CN=demo"
    exc.cert_expires = "2025-01-01T00:00:00+00:00"
    return exc


def _raising_opener(exc, calls):
    def opener(url):
        calls.append(url)
        raise exc

    return opener


class CertErrorTest(unittest.TestCase):
    def tearDown(self):
        clear_allowlist()
        set_audit_log(None)
        set_unverified_opener(None)

    def test_expired_classified(self):
        calls = []
        set_opener(_raising_opener(_cert_exc(10, "certificate has expired"), calls))
        with self.assertRaises(CertError) as ctx:
            fetch("https://expired.example/", retries=3)
        exc = ctx.exception
        self.assertEqual(exc.kind, CERT_EXPIRED)
        self.assertEqual(exc.action, "rejected")
        self.assertEqual(exc.url, "https://expired.example/")
        self.assertEqual(exc.cert_subject, "CN=demo")
        self.assertEqual(exc.cert_expires, "2025-01-01T00:00:00+00:00")

    def test_hostname_mismatch_classified(self):
        calls = []
        set_opener(_raising_opener(
            _cert_exc(62, "Hostname mismatch, certificate is not valid "
                          "for 'shop.example'"), calls))
        with self.assertRaises(CertError) as ctx:
            fetch("https://shop.example/")
        self.assertEqual(ctx.exception.kind, CERT_HOSTNAME_MISMATCH)
        self.assertEqual(ctx.exception.action, "rejected")

    def test_incomplete_chain_classified(self):
        calls = []
        set_opener(_raising_opener(
            _cert_exc(20, "unable to get local issuer certificate"), calls))
        with self.assertRaises(CertError) as ctx:
            fetch("https://chain.example/")
        self.assertEqual(ctx.exception.kind, CERT_INCOMPLETE_CHAIN)
        self.assertEqual(ctx.exception.action, "rejected")

    def test_cert_error_not_retried(self):
        calls = []
        set_opener(_raising_opener(_cert_exc(10, "certificate has expired"), calls))
        with self.assertRaises(CertError):
            fetch("https://expired.example/", retries=5)
        self.assertEqual(len(calls), 1)

    def test_allowlist_bypass_writes_audit(self):
        calls = []
        set_opener(_raising_opener(_cert_exc(10, "certificate has expired"), calls))
        set_unverified_opener(lambda url: (200, "ok"))
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = os.path.join(tmp, "audit.jsonl")
            set_audit_log(audit_path)
            allow_host("expired.example", minutes=5, reason="incident-1")
            result = fetch("https://expired.example/")
            self.assertEqual(result, (200, "ok"))
            with open(audit_path, encoding="utf-8") as fh:
                records = [json.loads(line) for line in fh]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["event"], "allowlist_bypass")
        self.assertEqual(record["host"], "expired.example")
        self.assertEqual(record["kind"], CERT_EXPIRED)
        self.assertEqual(record["result"], "bypassed")
        self.assertEqual(record["allowlist_reason"], "incident-1")
        self.assertEqual(record["cert_subject"], "CN=demo")
        self.assertEqual(record["cert_expires"], "2025-01-01T00:00:00+00:00")

    def test_expired_allowlist_entry_not_used(self):
        calls = []
        set_opener(_raising_opener(_cert_exc(10, "certificate has expired"), calls))
        set_unverified_opener(lambda url: (200, "ok"))
        allow_host("expired.example", minutes=0, reason="stale")
        with self.assertRaises(CertError) as ctx:
            fetch("https://expired.example/")
        self.assertEqual(ctx.exception.action, "rejected")

    def test_bypass_failure_is_explicit(self):
        calls = []
        set_opener(_raising_opener(_cert_exc(10, "certificate has expired"), calls))

        def bad_unverified(url):
            raise ConnectionError("refused")

        set_unverified_opener(bad_unverified)
        allow_host("expired.example", minutes=5, reason="incident-2")
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = os.path.join(tmp, "audit.jsonl")
            set_audit_log(audit_path)
            with self.assertRaises(CertError) as ctx:
                fetch("https://expired.example/")
            self.assertEqual(ctx.exception.action, "bypass_failed")
            with open(audit_path, encoding="utf-8") as fh:
                records = [json.loads(line) for line in fh]
        self.assertEqual(records[0]["result"], "bypass_failed")
