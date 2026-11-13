import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest

from archivepack import pack, restore


class SlowStream:
    """每次最多返回 max_chunk 字节，用于验证流式分块读取。"""

    def __init__(self, data, max_chunk=1024):
        self._buf = io.BytesIO(data)
        self._max = max_chunk
        self.reads = 0

    def read(self, size=-1):
        self.reads += 1
        return self._buf.read(min(size, self._max) if size > 0 else self._max)


class FlakyStream:
    """读到 limit 字节后抛错，模拟中断。"""

    def __init__(self, data, limit):
        self._buf = io.BytesIO(data)
        self._limit = limit
        self._served = 0

    def read(self, size=-1):
        if self._served >= self._limit:
            raise IOError("connection reset")
        chunk = self._buf.read(min(size, self._limit - self._served))
        self._served += len(chunk)
        return chunk


class PackTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _objects(self):
        base = os.path.join(self.root, "objects")
        found = []
        for dirpath, _, names in os.walk(base):
            found.extend(os.path.join(dirpath, n) for n in names)
        return found

    def test_saves_by_content_hash_with_extension(self):
        result = pack([{"url": "http://shop/a.pdf", "data": b"AAA"}], self.root)
        digest = hashlib.sha256(b"AAA").hexdigest()
        self.assertTrue(result["paths"][0].endswith(digest + ".pdf"))
        self.assertEqual(result["added"], 1)

    def test_duplicate_name_different_content_both_kept(self):
        pack([{"url": "http://shop/a.pdf", "data": b"AAA"},
              {"url": "http://news/a.pdf", "data": b"BBB"}], self.root)
        self.assertEqual(len(restore(self.root)), 2)
        self.assertEqual(len(self._objects()), 2)

    def test_query_string_stripped_from_name(self):
        result = pack([{"url": "http://shop/a.pdf?v=1#top", "data": b"AAA"}], self.root)
        self.assertEqual(result["entries"][0]["name"], "a.pdf")
        self.assertNotIn("?", result["paths"][0])

    def test_manifest_written_with_metadata(self):
        pack([{"url": "http://shop/a.pdf", "data": b"AAA"}], self.root)
        with open(os.path.join(self.root, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        entry = manifest["files"][0]
        self.assertEqual(entry["url"], "http://shop/a.pdf")
        self.assertEqual(entry["size"], 3)
        self.assertEqual(entry["sha256"], hashlib.sha256(b"AAA").hexdigest())
        self.assertTrue(entry["path"].startswith("objects" + os.sep))

    def test_path_traversal_and_unsafe_chars_sanitized(self):
        result = pack([
            {"url": "http://shop/../../etc/passwd", "data": b"X"},
            {"url": "http://shop/my file!.pdf", "data": b"Y"},
        ], self.root)
        for path in result["paths"]:
            self.assertTrue(os.path.realpath(path).startswith(os.path.realpath(self.root)))
        names = [e["name"] for e in result["entries"]]
        self.assertNotIn("..", names[0])
        self.assertEqual(names[1], "my_file_.pdf")

    def test_streaming_write_from_file_like(self):
        data = b"x" * (200 * 1024)
        stream = SlowStream(data)
        result = pack([{"url": "http://shop/big.bin", "data": stream}], self.root)
        self.assertGreater(stream.reads, 1)
        self.assertEqual(result["entries"][0]["size"], len(data))
        with open(result["paths"][0], "rb") as handle:
            self.assertEqual(handle.read(), data)

    def test_interrupted_write_resumes(self):
        data = b"0123456789" * 1000
        url = "http://shop/big.bin"
        first = pack([{"url": url, "data": FlakyStream(data, 5000)}], self.root)
        self.assertEqual(first["failed"], 1)
        part = hashlib.sha256(url.encode()).hexdigest() + ".part"
        self.assertTrue(os.path.exists(os.path.join(self.root, ".tmp", part)))
        second = pack([{"url": url, "data": data}], self.root)
        self.assertEqual(second["added"], 1)
        self.assertEqual(second["entries"][0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(os.listdir(os.path.join(self.root, ".tmp")), [])

    def test_cross_batch_dedup_with_refcount(self):
        pack([{"url": "http://shop/a.pdf", "data": b"AAA"}], self.root)
        result = pack([{"url": "http://news/a.pdf", "data": b"AAA"},
                       {"url": "http://news/b.pdf", "data": b"BBB"}], self.root)
        self.assertEqual(result["reused"], 1)
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(self._objects()), 2)
        with open(os.path.join(self.root, "refs.json"), encoding="utf-8") as handle:
            refs = json.load(handle)
        self.assertEqual(refs[hashlib.sha256(b"AAA").hexdigest()], 2)
        self.assertEqual(len(restore(self.root)), 3)

    def test_restore_reports_ok_missing_corrupt(self):
        result = pack([{"url": "http://shop/a.pdf", "data": b"AAA"},
                       {"url": "http://shop/b.pdf", "data": b"BBB"},
                       {"url": "http://shop/c.pdf", "data": b"CCC"}], self.root)
        rows = restore(self.root)
        self.assertEqual([r["status"] for r in rows], ["ok"] * 3)
        os.remove(result["paths"][0])
        with open(result["paths"][1], "ab") as handle:
            handle.write(b"tamper")
        rows = restore(self.root)
        self.assertEqual([r["status"] for r in rows], ["missing", "corrupt", "ok"])

    def test_failed_item_counted_and_skipped(self):
        result = pack([{"url": "http://shop/a.pdf", "data": FlakyStream(b"AAA", 0)},
                       {"url": "http://shop/b.pdf", "data": b"BBB"}], self.root)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(restore(self.root)), 1)


if __name__ == "__main__":
    unittest.main()
