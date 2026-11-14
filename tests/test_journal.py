import os
import shutil
import tempfile
import unittest

from crawljournal import append, recover


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "journal.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_append_recover(self):
        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        self.assertEqual([r["id"] for r in recover(self.path)], [1, 2])

    def test_half_line_raises(self):
        append({"id": 1}, self.path)
        open(self.path, "a", encoding="utf-8").write('{"id": 2')
        with self.assertRaises(ValueError):
            recover(self.path)

    def test_no_checksum(self):
        append({"id": 1}, self.path)
        self.assertNotIn("checksum", recover(self.path)[0])

    def test_torn_tail_truncated(self):
        from crawljournal.journal import _encode_frame

        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        frame = _encode_frame({"id": 3}, 3)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(frame[: len(frame) // 2])
        result = recover(self.path)
        self.assertEqual([r["id"] for r in result], [1, 2])
        self.assertEqual(result.dropped, 1)
        self.assertEqual(result.last_seq, 2)

    def test_bad_checksum_discarded(self):
        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        with open(self.path, encoding="utf-8") as handle:
            content = handle.read()
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(content.replace('"id": 2', '"id": 9'))
        result = recover(self.path)
        self.assertEqual([r["id"] for r in result], [1])
        self.assertEqual(result.dropped, 1)
        self.assertEqual(result.last_seq, 1)

    def test_sequence_gap_keeps_last_contiguous(self):
        from crawljournal.journal import _encode_frame

        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(_encode_frame({"id": 4}, 4))
        result = recover(self.path)
        self.assertEqual([r["id"] for r in result], [4])
        self.assertEqual(result.dropped, 2)
        self.assertEqual(result.last_seq, 4)

    def test_duplicate_seq_deduped(self):
        from crawljournal.journal import _encode_frame

        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(_encode_frame({"id": 22}, 2))
            handle.write(_encode_frame({"id": 3}, 3))
        result = recover(self.path)
        self.assertEqual([r["id"] for r in result], [1, 22, 3])
        self.assertEqual(result.dropped, 1)
        self.assertEqual(result.last_seq, 3)
