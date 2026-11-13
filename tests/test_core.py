import os
import tempfile
import unittest

from csvexport import ExportError, export


class CsvexportTest(unittest.TestCase):
    def test_writes_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["a", "b"], [1, 2]], path)
            self.assertEqual(open(path).read().splitlines(), ["a,b", "1,2"])

    def test_encoding_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["中文"]], path, {"encoding": "utf-8"})
            self.assertIn("中文", open(path, encoding="utf-8").read())

    def test_default_bom_for_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["姓名"], ["张三"]], path)
            self.assertTrue(open(path, "rb").read().startswith(b"\xef\xbb\xbf"))

    def test_no_bom_for_pure_ascii(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["a", "b"]], path)
            self.assertFalse(open(path, "rb").read().startswith(b"\xef\xbb\xbf"))

    def test_long_number_as_text(self):
        import csv
        import io

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["123456789012345678", 123456789012345678, 42]], path)
            with open(path, encoding="utf-8", newline="") as handle:
                cells = next(csv.reader(handle))
            self.assertEqual(
                cells, ['="123456789012345678"', '="123456789012345678"', "42"]
            )

    def test_zero_leading_id_as_text(self):
        import csv

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["007", "0", "0.5"]], path)
            with open(path, encoding="utf-8", newline="") as handle:
                cells = next(csv.reader(handle))
            self.assertEqual(cells, ['="007"', "0", "0.5"])

    def test_special_chars_escaped(self):
        import csv
        import io

        dirty = ['含,逗号', '含"引号"', "含\n换行", "三\r\n者\t混合"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export([["h1", "h2", "h3", "h4"], dirty], path)
            with open(path, encoding="utf-8", newline="") as handle:
                text = handle.read()
            rows = list(csv.reader(io.StringIO(text)))
            self.assertEqual(rows, [["h1", "h2", "h3", "h4"], dirty])

    def test_dialect_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export(
                [["a;b", "c"]],
                path,
                {"delimiter": ";", "quotechar": "'", "lineterminator": "\n"},
            )
            self.assertEqual(open(path, encoding="utf-8").read(), "'a;b';c\n")

    def test_streaming_generator(self):
        def big():
            for i in range(50000):
                yield ["row", i]

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export(big(), path)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(sum(1 for _ in handle), 50000)

    def test_column_count_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            with self.assertRaises(ExportError) as ctx:
                export([["a", "b"], ["only-one"]], path, {"columns": ["c1", "c2"]})
            self.assertIn("row 2", str(ctx.exception))
            self.assertIn("c1", str(ctx.exception))

    def test_required_column_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            with self.assertRaises(ExportError) as ctx:
                export(
                    [["A001", "10"], ["A002", ""]],
                    path,
                    {"columns": ["单号", "金额"], "required": ["金额"]},
                )
            message = str(ctx.exception)
            self.assertIn("row 2", message)
            self.assertIn("金额", message)

    def test_dict_rows_follow_column_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            export(
                [{"b": 2, "a": 1}],
                path,
                {"columns": ["a", "b"], "lineterminator": "\n"},
            )
            self.assertEqual(open(path, encoding="utf-8").read(), "a,b\n1,2\n")

    def test_shards_with_checksum_and_resume(self):
        import json
        import zlib

        rows = [["r%d" % i, "值%d" % i] for i in range(7)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.csv")
            export(rows[:4], path, {"columns": ["k", "v"], "shard_size": 3})
            export(rows, path, {"columns": ["k", "v"], "shard_size": 3})

            with open(os.path.join(tmp, "out.manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["total_rows"], 7)
            self.assertEqual([s["rows"] for s in manifest["shards"]], [3, 1, 3])
            for shard in manifest["shards"]:
                data = open(shard["file"], "rb").read()
                self.assertEqual(zlib.crc32(data), shard["crc32"])

            import csv
            import io

            merged = []
            for shard in manifest["shards"]:
                text = open(shard["file"], encoding="utf-8-sig").read()
                merged.extend(list(csv.reader(io.StringIO(text)))[1:])
            self.assertEqual(merged, rows)

    def test_resume_detects_corrupt_shard(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.csv")
            export([["a"], ["b"]], path, {"shard_size": 1})
            with open(os.path.join(tmp, "out.0001.csv"), "ab") as f:
                f.write(b"tampered")
            with self.assertRaises(ExportError):
                export([["a"], ["b"]], path, {"shard_size": 1})
