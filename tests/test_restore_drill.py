"""隔离恢复演练的新增测试：恢复成功、缺块失败、校验差异、隔离性。

不改动 test_drill.py 的既有断言。
"""

import hashlib
import importlib
import json
import os
import shutil
import tempfile
import unittest

from restoredrill import configure, drill, report

module = importlib.import_module("restoredrill.drill")


def make_snapshot(root, name, tables, key_tables=None,
                  created_at="2026-11-15T00:00:00+00:00"):
    """造一份快照：chunks/<表>-0001.csv + manifest.json。"""
    snap = os.path.join(root, name)
    chunks_dir = os.path.join(snap, "chunks")
    os.makedirs(chunks_dir)
    manifest = {
        "snapshot": name,
        "created_at": created_at,
        "key_tables": list(key_tables or []),
        "tables": {},
    }
    for table, rows in tables.items():
        data = "".join(r + "\n" for r in rows).encode("utf-8")
        fname = "%s-0001.csv" % table
        with open(os.path.join(chunks_dir, fname), "wb") as fh:
            fh.write(data)
        manifest["tables"][table] = {
            "chunks": [fname],
            "rows": len(rows),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    with open(os.path.join(snap, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return snap


class IsolatedDrillTest(unittest.TestCase):
    def setUp(self):
        module._history.clear()
        self.tmp = tempfile.mkdtemp(prefix="restoredrill-test-")
        self.archive = os.path.join(self.tmp, "archive.jsonl")
        configure(snapshot_root=self.tmp, production_dir=None,
                  archive_path=self.archive)

    def tearDown(self):
        configure(snapshot_root=None, production_dir=None,
                  archive_path=module._DEFAULT_ARCHIVE)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restore_success(self):
        make_snapshot(self.tmp, "snap-ok",
                      {"users": ["u1", "u2", "u3"], "orders": ["o1"]},
                      key_tables=["users"])
        result = drill("snap-ok")
        self.assertTrue(result["ok"])
        self.assertEqual(result["conclusion"], "PASS")
        self.assertEqual(result["checks"]["users"]["status"], "ok")
        self.assertEqual(result["checks"]["users"]["actual_rows"], 3)
        self.assertTrue(result["checks"]["users"]["key"])
        self.assertIsNotNone(result["rto_seconds"])
        self.assertIsNotNone(result["rpo_seconds"])
        self.assertFalse(result["kept_scene"])
        self.assertFalse(os.path.exists(result["target"]))  # 成功即清理

    def test_missing_chunk_fails_and_keeps_scene(self):
        snap = make_snapshot(self.tmp, "snap-broken", {"users": ["u1"]})
        os.remove(os.path.join(snap, "chunks", "users-0001.csv"))
        result = drill("snap-broken")
        self.assertFalse(result["ok"])
        self.assertEqual(result["conclusion"], "FAIL")
        self.assertTrue(any("缺块" in e for e in result["errors"]))
        self.assertEqual(result["checks"]["users"]["status"], "missing-chunk")
        self.assertTrue(result["kept_scene"])
        self.assertTrue(os.path.isdir(result["target"]))  # 失败保留现场

    def test_checksum_mismatch_reported_as_diff(self):
        snap = make_snapshot(self.tmp, "snap-diff", {"users": ["u1", "u2"]})
        with open(os.path.join(snap, "chunks", "users-0001.csv"), "ab") as fh:
            fh.write(b"u3-corrupted\n")
        result = drill("snap-diff")
        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["users"]["status"], "checksum-mismatch")
        fields = {d["field"] for d in result["diffs"] if d["table"] == "users"}
        self.assertIn("sha256", fields)
        self.assertTrue(result["kept_scene"])

    def test_isolation_from_production_dir(self):
        production = os.path.join(self.tmp, "production")
        os.makedirs(production)
        sentinel = os.path.join(production, "live-data.txt")
        with open(sentinel, "w", encoding="utf-8") as fh:
            fh.write("do-not-touch")
        configure(snapshot_root=self.tmp, production_dir=production,
                  archive_path=self.archive)
        make_snapshot(self.tmp, "snap-iso", {"users": ["u1"]})
        result = drill("snap-iso")
        self.assertTrue(result["ok"])
        with open(sentinel, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "do-not-touch")
        # 恢复目标不在生产目录内
        self.assertFalse(os.path.abspath(result["target"]).startswith(
            os.path.abspath(production) + os.sep))

    def test_report_query_by_snapshot_and_table(self):
        make_snapshot(self.tmp, "snap-a", {"users": ["u1"]})
        make_snapshot(self.tmp, "snap-b", {"orders": ["o1", "o2"]})
        drill("snap-a")
        drill("snap-b")
        self.assertEqual(report()["drills"], 2)
        by_snap = report(snapshot="snap-a")
        self.assertEqual(by_snap["drills"], 1)
        self.assertEqual(by_snap["results"][0]["snapshot"], "snap-a")
        by_table = report(table="orders")
        self.assertEqual(by_table["drills"], 1)
        self.assertEqual(by_table["tables"]["orders"][0]["actual_rows"], 2)
        self.assertEqual(report(table="users")["tables"]["users"][0]["snapshot"],
                         "snap-a")

    def test_archive_written(self):
        make_snapshot(self.tmp, "snap-log", {"users": ["u1"]})
        drill("snap-log")
        self.assertTrue(os.path.isfile(self.archive))
        with open(self.archive, "r", encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["snapshot"], "snap-log")
        self.assertEqual(lines[0]["conclusion"], "PASS")


if __name__ == "__main__":
    unittest.main()
