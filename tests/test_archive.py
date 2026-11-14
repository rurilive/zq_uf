import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest

from attacharchive import reconcile, upload
from attacharchive import archive as impl


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_upload_reads_all(self):
        self.assertEqual(upload("a.bin", io.BytesIO(b"abc"), self.dir)["size"], 3)

    def test_no_resume(self):
        self.assertNotIn("chunks", upload("a.bin", io.BytesIO(b"abc"), self.dir))

    def test_reconcile_always_clean(self):
        self.assertEqual(reconcile([{"key": "a.bin"}]), {"missing": [], "orphans": [], "corrupt": []})


class _FlakyStream:
    """读到 cut 字节后模拟断连。"""

    def __init__(self, data, cut):
        self._data = data
        self._cut = cut
        self._pos = 0

    def read(self, size=-1):
        if self._pos >= self._cut:
            raise ConnectionError("模拟断连")
        end = self._cut if size < 0 else min(self._pos + size, self._cut)
        piece = self._data[self._pos:end]
        self._pos = end
        return piece


class ChunkedArchiveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _entries(self):
        with open(os.path.join(self.dir, "manifest.json"), encoding="utf-8") as handle:
            stored = json.load(handle)
        return [dict(key=k, **v) for k, v in stored["files"].items()]

    def _object(self, key):
        with open(impl._object_path(self.dir, key), "rb") as handle:
            return handle.read()

    def test_resume_retransmits_only_missing_chunks(self):
        data = bytes(range(256)) * 64  # 16384 字节
        chunk = 2048  # 8 片
        cut = chunk * 3 + 50  # 写完 3 片后断连
        with self.assertRaises(ConnectionError):
            upload("big.bin", _FlakyStream(data, cut), self.dir, chunk_size=chunk)
        result = upload("big.bin", io.BytesIO(data), self.dir, chunk_size=chunk)
        self.assertEqual(result["parts"], 8)
        self.assertEqual(result["resumed"], 3)
        self.assertEqual(result["retransmitted"], 5)
        self.assertEqual(self._object("big.bin"), data)

    def test_merge_out_of_order(self):
        parts = [b"aaa", b"bbb", b"ccc"]
        digests = [hashlib.sha256(p).hexdigest() for p in parts]
        chunk_dir = impl._chunk_dir(self.dir, "k.bin")
        os.makedirs(chunk_dir)
        for index in (2, 0, 1):  # 乱序落盘
            with open(os.path.join(chunk_dir, "%08d.part" % index), "wb") as handle:
                handle.write(parts[index])
        impl._save_state(self.dir, "k.bin", {
            "chunk_size": 3,
            "hashes": digests,
            "sha256": hashlib.sha256(b"aaabbbccc").hexdigest(),
        })
        path = impl._merge(self.dir, "k.bin")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"aaabbbccc")

    def test_manifest_reconcile(self):
        upload("a.bin", io.BytesIO(b"aaa" * 100), self.dir)
        upload("b.bin", io.BytesIO(b"bbb" * 100), self.dir)
        entries = self._entries()
        self.assertEqual(reconcile(entries, self.dir),
                         {"missing": [], "orphans": [], "corrupt": []})
        os.remove(impl._object_path(self.dir, "a.bin"))
        with open(impl._object_path(self.dir, "b.bin"), "ab") as handle:
            handle.write(b"x")
        with open(impl._object_path(self.dir, "ghost.bin"), "wb") as handle:
            handle.write(b"g")
        result = reconcile(entries, self.dir)
        self.assertEqual(result["missing"], ["a.bin"])
        self.assertEqual(result["corrupt"], ["b.bin"])
        self.assertEqual(result["orphans"], ["ghost.bin"])

    def test_orphan_cleanup_and_repair(self):
        upload("a.bin", io.BytesIO(b"aaa"), self.dir)
        ghost = impl._object_path(self.dir, "ghost.bin")
        with open(ghost, "wb") as handle:
            handle.write(b"g")
        with open(impl._object_path(self.dir, "a.bin"), "ab") as handle:
            handle.write(b"corrupt")
        result = reconcile(self._entries(), self.dir, repair=True)
        self.assertEqual(sorted(result["repaired"]), ["a.bin", "ghost.bin"])
        self.assertFalse(os.path.exists(ghost))
        followup = reconcile(self._entries(), self.dir)
        self.assertEqual(followup["orphans"], [])
        self.assertEqual(followup["corrupt"], [])
        self.assertEqual(followup["missing"], ["a.bin"])  # 坏对象已删，待重传

    def test_idempotent_completion(self):
        data = bytes(range(256)) * 32
        first = upload("a.bin", io.BytesIO(data), self.dir, chunk_size=4096)
        second = upload("a.bin", io.BytesIO(data), self.dir, chunk_size=4096)
        self.assertEqual(second["status"], "exists")
        self.assertEqual(second["retransmitted"], 0)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["parts"], second["parts"])
        self.assertEqual(os.listdir(os.path.join(self.dir, "objects")), ["a.bin"])
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(reconcile(entries, self.dir),
                         {"missing": [], "orphans": [], "corrupt": []})
