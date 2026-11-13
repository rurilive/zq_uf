import os
import shutil
import tempfile
import tracemalloc
import unittest

from textnorm import normalize


class NormalizeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "a.csv")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, data):
        open(self.path, "wb").write(data)

    def read_out(self, result):
        with open(result["out"], "rb") as handle:
            return handle.read()

    def test_bom_stripped(self):
        self.write(b"\xef\xbb\xbfid,name\n")
        result = normalize(self.path)
        self.assertEqual(result["text"], "id,name\n")
        self.assertEqual(result["encoding"], "utf-8")
        self.assertTrue(result["had_bom"])
        self.assertFalse(self.read_out(result).startswith(b"\xef\xbb\xbf"))

    def test_crlf_to_lf(self):
        self.write(b"a\r\nb\r\n")
        self.assertEqual(normalize(self.path)["text"], "a\nb\n")

    def test_cr_to_lf(self):
        self.write(b"a\rb\rc")
        result = normalize(self.path)
        self.assertEqual(result["text"], "a\nb\nc")
        self.assertEqual(result["lines"], 2)

    def test_mixed_newlines(self):
        self.write(b"a\rb\r\nc\n")
        result = normalize(self.path)
        self.assertEqual(result["text"], "a\nb\nc\n")
        self.assertEqual(result["lines"], 3)

    def test_gbk_detected(self):
        self.write("编号,名称\r\n第二行\r\n".encode("gbk"))
        result = normalize(self.path)
        self.assertEqual(result["text"], "编号,名称\n第二行\n")
        self.assertEqual(result["encoding"], "gb18030")
        self.assertEqual(result["bad_lines"], [])

    def test_encoding_hint_respected(self):
        self.write("caf\xe9\n".encode("latin-1"))
        result = normalize(self.path, encoding="latin-1")
        self.assertEqual(result["text"], "caf\xe9\n")

    def test_line_map(self):
        self.write(b"a\rb\r\nc\n")
        result = normalize(self.path)
        self.assertEqual(result["line_map"], [1, 2, 3])

    def test_line_map_without_trailing_newline(self):
        self.write(b"a\nb\n")
        self.assertEqual(normalize(self.path)["line_map"], [1, 2])
        self.write(b"a\nb")
        self.assertEqual(normalize(self.path)["line_map"], [1, 2])

    def test_undecodable_line_kept_and_marked(self):
        good = ("行,值\n".encode("utf-8")) * 20000  # 超过嗅探样本，确保选中 utf-8
        self.write(good + b"bad\xff\xfe\n" + b"tail\n")
        result = normalize(self.path)
        self.assertEqual(result["encoding"], "utf-8")
        self.assertEqual(result["bad_lines"], [20001])
        self.assertIn("\ufffd", result["text"].split("\n")[20000])
        self.assertEqual(result["lines"], 20002)

    def test_streaming_memory_bounded(self):
        line = "x" * 4096 + "\n"
        with open(self.path, "w", encoding="utf-8") as handle:
            for _ in range(2000):  # 约 8 MB，行数少以隔离 line_map 开销
                handle.write(line)
        tracemalloc.start()
        try:
            result = normalize(self.path, keep_text=False)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertIsNone(result["text"])
        self.assertEqual(result["lines"], 2000)
        self.assertLess(peak, 1024 * 1024)

    def test_idempotent(self):
        self.write("\ufeffid,name\r\n1,键盘\r\n".encode("utf-8"))
        first = normalize(self.path)
        second = normalize(first["out"])
        self.assertEqual(self.read_out(first), self.read_out(second))
        self.assertEqual(second["encoding"], "utf-8")
        self.assertFalse(second["had_bom"])
        self.assertEqual(second["bad_lines"], [])
