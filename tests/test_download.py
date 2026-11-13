import os
import tempfile
import unittest
import hashlib
import io
import threading

from filefetch import download, SiteLimiter, VerificationError


class FilefetchTest(unittest.TestCase):
    def test_writes_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            size = download("http://file/a", path, lambda url: b"12345")
            self.assertEqual(size, 5)
            self.assertEqual(open(path, "rb").read(), b"12345")

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            open(path, "wb").write(b"old")
            download("http://file/a", path, lambda url: b"new")
            self.assertEqual(open(path, "rb").read(), b"new")

    def test_empty_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            self.assertEqual(download("http://file/a", path, lambda url: b""), 0)


class FakeResponse:
    def __init__(self, payload, status=None, headers=None, truncated=False):
        self._stream = io.BytesIO(payload)
        self.status = status
        self.headers = headers or {}
        self._truncated = truncated
        self.closed = False

    def read(self, size=-1):
        chunk = self._stream.read(size)
        if self._truncated and chunk == b"":
            raise ConnectionError("连接中断")
        return chunk

    def close(self):
        self.closed = True
        self._stream.close()


class RangeServer:
    """记录每次请求的 Range 头，可选在断点处截断响应模拟断连。"""

    def __init__(self, payload, supports=True, truncate_before=None):
        self.payload = payload
        self.supports = supports
        self.truncate_before = set(truncate_before or [])
        self.requests = []

    def __call__(self, url, headers=None):
        headers = headers or {}
        self.requests.append(headers.get("Range"))
        range_value = headers.get("Range")
        is_probe = range_value == "bytes=0-0"

        if not self.supports:
            return FakeResponse(self.payload, status=200,
                                headers={"Content-Length": str(len(self.payload))})

        if is_probe:
            return FakeResponse(self.payload[:1], status=206,
                                headers={"Content-Range": "bytes 0-0/%d"
                                         % len(self.payload),
                                         "Content-Length": "1"})

        start = (int(range_value.split("=", 1)[1].split("-", 1)[0])
                 if range_value else 0)
        body = self.payload[start:]
        if start in self.truncate_before:
            body = body[:3]
        response = FakeResponse(
            body, status=206,
            headers={"Content-Range": "bytes %d-%d/%d"
                     % (start, len(self.payload) - 1, len(self.payload)),
                     "Content-Length": str(len(body))},
            truncated=start in self.truncate_before)
        return response


class ResumableDownloadTest(unittest.TestCase):
    URL = "http://file.example/attachment/report.bin"

    def test_resume_from_partial(self):
        payload = bytes(range(40))
        with tempfile.TemporaryDirectory() as tmp:
            final = os.path.join(tmp, "a.bin")
            part = final + ".part"
            server = RangeServer(payload, truncate_before=[0])
            with self.assertRaises(Exception):
                download(self.URL, final, server)
            self.assertTrue(os.path.exists(part))

            server.truncate_before.clear()
            size = download(self.URL, final, server)

            self.assertEqual(size, len(payload))
            with open(final, "rb") as handle:
                self.assertEqual(handle.read(), payload)
            self.assertFalse(os.path.exists(part))
            self.assertEqual(server.requests[-1], "bytes=%d-" % 3)

    def test_unsupported_range_restarts_from_zero(self):
        payload = b"x" * 20
        with tempfile.TemporaryDirectory() as tmp:
            part = os.path.join(tmp, "a.bin.part")
            with open(part, "wb") as handle:
                handle.write(b"stale")
            server = RangeServer(payload, supports=False)

            size = download("http://file/a", os.path.join(tmp, "a.bin"), server)

            self.assertEqual(size, len(payload))
            self.assertEqual(open(os.path.join(tmp, "a.bin"), "rb").read(), payload)
            self.assertFalse(os.path.exists(part))
            self.assertEqual([r for r in server.requests if r != "bytes=0-0"], [None])

    def test_hash_naming_avoids_overwrite(self):
        payload = b"same content" * 4
        with tempfile.TemporaryDirectory() as tmp:
            first = download("http://site/a/report.bin", tmp, lambda url: payload)
            second = download("http://site/b/report.bin", tmp, lambda url: payload)
            files = sorted(os.listdir(tmp))

            self.assertEqual(first, second)
            self.assertEqual(len(files), 1)
            expected = hashlib.sha256(payload).hexdigest() + ".bin"
            self.assertEqual(files, [expected])
            self.assertFalse(any(name == "report.bin" for name in files))

    def test_checksum_failure_keeps_partial_file(self):
        payload = b"1234567890"
        with tempfile.TemporaryDirectory() as tmp:
            part = os.path.join(tmp, "a.bin.part")
            with self.assertRaises(VerificationError):
                download("http://file/a", os.path.join(tmp, "a.bin"),
                         lambda url: payload,
                         checksum=("sha256", "deadbeef"))

            self.assertTrue(os.path.exists(part))
            self.assertEqual(open(part, "rb").read(), payload)
            self.assertFalse(os.path.exists(os.path.join(tmp, "a.bin")))

    def test_length_mismatch_keeps_partial_file(self):
        payload = b"abc"

        def open_url(url, headers=None):
            return FakeResponse(payload, status=200,
                                headers={"Content-Length": "9"})

        with tempfile.TemporaryDirectory() as tmp:
            part = os.path.join(tmp, "a.bin.part")
            with self.assertRaises(VerificationError):
                download("http://file/a", os.path.join(tmp, "a.bin"), open_url)
            self.assertTrue(os.path.exists(part))
            self.assertFalse(os.path.exists(os.path.join(tmp, "a.bin")))


class SiteLimiterTest(unittest.TestCase):
    def test_per_host_concurrency_is_limited(self):
        limiter = SiteLimiter(per_host=2)
        active = 0
        max_active = 0
        guard = threading.Lock()
        pair = threading.Barrier(2)

        def open_url(url, headers=None):
            nonlocal active, max_active
            if headers and headers.get("Range") == "bytes=0-0":
                return FakeResponse(b"x", status=206)
            with guard:
                active += 1
                max_active = max(max_active, active)
            pair.wait()
            with guard:
                active -= 1
            return FakeResponse(b"ok", headers={"Content-Length": "2"})

        errors = []

        def worker(index):
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    download("http://same.host/file%d" % index, tmp,
                             open_url, limiter=limiter)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(max_active, 2)

    def test_different_hosts_are_independent(self):
        limiter = SiteLimiter(per_host=1)
        observed = []
        guard = threading.Lock()
        release = threading.Event()

        def open_url(url, headers=None):
            if headers and headers.get("Range") == "bytes=0-0":
                return FakeResponse(b"x", status=206)
            with guard:
                observed.append(url)
            if "slow" in url:
                release.wait(5)
            return FakeResponse(b"ok", headers={"Content-Length": "2"})

        with tempfile.TemporaryDirectory() as tmp:
            slow = threading.Thread(
                target=download,
                args=("http://slow.host/a", tmp, open_url),
                kwargs={"limiter": limiter})
            fast = threading.Thread(
                target=download,
                args=("http://fast.host/b", tmp, open_url),
                kwargs={"limiter": limiter})
            slow.start()
            fast.start()
            fast.join(timeout=5)
            release.set()
            slow.join(timeout=5)

        self.assertFalse(slow.is_alive())
        self.assertFalse(fast.is_alive())
        self.assertEqual(len(observed), 2)

    def test_single_slot_same_host_does_not_deadlock(self):
        limiter = SiteLimiter(per_host=1)
        gate = threading.Event()

        def open_url(url, headers=None):
            if headers and headers.get("Range") == "bytes=0-0":
                return FakeResponse(b"x", status=206)
            gate.wait(5)
            return FakeResponse(b"ok", headers={"Content-Length": "2"})

        errors = []

        def worker(index):
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    download("http://one.host/file%d" % index, tmp,
                             open_url, limiter=limiter)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        gate.set()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
