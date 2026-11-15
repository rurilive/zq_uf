import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from seedscore.__main__ import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.seeds = os.path.join(self.tmp.name, "seeds.json")
        with open(self.seeds, "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {"url": "https://a.example/", "source": "manual"},
                    {"url": "https://b.example/login/cart/1/2/3/4/5", "source": "referral"},
                    {"url": "https://c.example/", "status": "dead"},
                ],
                handle,
            )

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(list(argv))
        return code, buffer.getvalue()

    def test_score(self):
        code, out = self.run_cli("score", self.seeds)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 3)
        self.assertIn("parts", payload[0])

    def test_filter_with_export(self):
        path = os.path.join(self.tmp.name, "report.csv")
        code, out = self.run_cli(
            "filter", self.seeds, "--export", path, "--export-format", "csv"
        )
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(len(report["kept"]), 1)
        self.assertEqual(len(report["dead"]), 1)
        self.assertTrue(os.path.exists(path))

    def test_restore_and_apply(self):
        log = os.path.join(self.tmp.name, "log.jsonl")
        code, _ = self.run_cli(
            "restore",
            "https://b.example/login/cart/1/2/3/4/5",
            "--log",
            log,
            "--actor",
            "alice",
            "--reason",
            "false kill",
        )
        self.assertEqual(code, 0)
        code, out = self.run_cli("filter", self.seeds, "--log", log)
        self.assertEqual(code, 0)
        kept = json.loads(out)["kept"]
        self.assertTrue(any(item.get("restored") for item in kept))
        self.assertEqual(len(kept), 2)

    def test_query_exports(self):
        log = os.path.join(self.tmp.name, "log.jsonl")
        self.run_cli("restore", "https://a.example/", "--log", log, "--actor", "alice", "--reason", "r")
        out_csv = os.path.join(self.tmp.name, "q.csv")
        code, out = self.run_cli(
            "query", "--log", log, "--export", out_csv, "--export-format", "csv"
        )
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out_csv))
        self.assertEqual(len(json.loads(out)), 1)

    def test_replay(self):
        log = os.path.join(self.tmp.name, "log.jsonl")
        self.run_cli(
            "restore", "https://a.example/", "--log", log, "--actor", "alice",
            "--reason", "r", "--at", "2026-01-01T00:00:00Z",
        )
        code, out = self.run_cli("replay", "--log", log, "--until", "2025-01-01T00:00:00Z")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["restored"], [])
        code, out = self.run_cli("replay", "--log", log)
        self.assertEqual(json.loads(out)["restored"], ["a.example/"])

    def test_shadow(self):
        human = os.path.join(self.tmp.name, "human.json")
        with open(human, "w", encoding="utf-8") as handle:
            json.dump([{"url": "https://a.example/"}], handle)
        code, out = self.run_cli("shadow", self.seeds, "--human", human, "--sample-size", "10")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["verdict"], "safe")

    def test_missing_file_returns_error_code(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["score", os.path.join(self.tmp.name, "nope.json")])
        self.assertEqual(code, 2)
        self.assertIn("seedscore:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
