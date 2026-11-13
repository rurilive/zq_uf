import gzip
import os
import tempfile
import unittest

from sitemaptool import open_stream, parse_urlset


class ReadTracker:
    """记录底层 read 的最大块大小，整包读入会直接抛错。"""

    def __init__(self, handle, limit=131072):
        self._handle = handle
        self._limit = limit
        self.max_read = 0

    def read(self, size=-1):
        if size is None or size < 0 or size > self._limit:
            raise AssertionError(f"出现非流式读取，块大小={size}")
        self.max_read = max(self.max_read, size)
        data = self._handle.read(size)
        return data

    def peek(self, size):
        return self._handle.peek(size)

    def close(self):
        self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


class GzipStreamTest(unittest.TestCase):
    def test_gz_decompressed_in_small_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pages.xml.gz")
            with gzip.open(path, "wb") as handle:
                handle.write(b'<?xml version="1.0"?><urlset>')
                for index in range(40000):
                    handle.write(
                        (
                            f"<url><loc>http://shop.example/p{index}"
                            f"-{index:08d}-detail-page</loc></url>"
                        ).encode()
                    )
                handle.write(b"</urlset>")

            tracker = ReadTracker(open(path, "rb"))
            with open_stream(tracker) as stream:
                entries = parse_urlset(stream)
            file_size = os.path.getsize(path)

        self.assertEqual(len(entries), 40000)
        self.assertEqual(entries[0]["loc"], "http://shop.example/p0-00000000-detail-page")
        self.assertGreater(tracker.max_read, 0)
        self.assertLessEqual(tracker.max_read, 131072)
        self.assertGreater(file_size, tracker.max_read)


if __name__ == "__main__":
    unittest.main()
