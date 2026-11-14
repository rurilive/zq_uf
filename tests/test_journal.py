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


class WalRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "journal.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _lines(self):
        with open(self.path, "r", encoding="utf-8") as handle:
            return handle.read().splitlines(keepends=True)

    def _corrupt_checksum(self, index):
        lines = self._lines()
        marker = '"checksum":"'
        pos = lines[index].index(marker) + len(marker)
        flipped = "0" if lines[index][pos] != "0" else "1"
        lines[index] = lines[index][:pos] + flipped + lines[index][pos + 1:]
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)

    def test_half_line_truncated(self):
        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        with open(self.path, "a", encoding="utf-8") as handle:  # 模拟写一半崩溃
            handle.write('{"len": 9, "seq": 3, "checksum": "00000000", "da')
        result = recover(self.path)
        self.assertEqual([r["id"] for r in result], [1, 2])
        self.assertEqual(result.discarded, 1)
        self.assertEqual(result.last_seq, 2)
        with open(self.path, "rb") as handle:  # 尾部半行已被截断
            self.assertTrue(handle.read().endswith(b"\n"))

    def test_bad_checksum_tail(self):
        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        self._corrupt_checksum(-1)
        result = recover(self.path)
        self.assertEqual([r["id"] for r in result], [1])
        self.assertEqual(result.discarded, 1)
        self.assertEqual(result.last_seq, 1)

    def test_bad_checksum_middle_keeps_last_run(self):
        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        append({"id": 3}, self.path)
        self._corrupt_checksum(1)  # 序号 2 校验失败，连续性断开
        result = recover(self.path)
        self.assertEqual([r["id"] for r in result], [3])
        self.assertEqual(result.seqs, [3])
        self.assertEqual(result.discarded, 2)

    def test_sequence_continuous_after_crash(self):
        for i in range(3):
            append({"id": i + 1}, self.path)
        self.assertEqual(recover(self.path).seqs, [1, 2, 3])
        with open(self.path, "a", encoding="utf-8") as handle:  # 崩溃半行
            handle.write('{"len": 9, "seq": 4, "checksum": "00000000", "da')
        recover(self.path)
        append({"id": 4}, self.path)  # 序号应续上 4，不重用不留洞
        result = recover(self.path)
        self.assertEqual(result.seqs, [1, 2, 3, 4])
        self.assertEqual([r["id"] for r in result], [1, 2, 3, 4])

    def test_duplicate_seq_kept_once(self):
        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        lines = self._lines()
        with open(self.path, "a", encoding="utf-8") as handle:  # 重复 seq=1
            handle.write(lines[0])
        result = recover(self.path)
        self.assertEqual([r["id"] for r in result], [1, 2])
        self.assertEqual(result.seqs, [1, 2])
        self.assertEqual(result.discarded, 1)
