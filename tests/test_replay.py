import os
import json
import sqlite3
import tempfile
import unittest

from replayfail import record, replay


class ReplayfailTest(unittest.TestCase):
    def test_record_writes_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            record({"url": "http://a"}, path)
            self.assertEqual(len(open(path, encoding="utf-8").read().splitlines()), 1)

    def test_replay_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            record({"url": "http://ok"}, path)
            record({"url": "http://bad"}, path)
            counts = replay([path], lambda url: url.endswith("ok"))
        self.assertEqual(counts, {"fixed": 1, "still_failed": 1})

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            open(path, "w").close()
            self.assertEqual(replay([path], lambda url: True), {"fixed": 0, "still_failed": 0})


class ReplayRegressionTest(unittest.TestCase):
    def _write_sample(self, path, **overrides):
        failure = {
            "method": "POST",
            "url": "http://site/api",
            "headers": {"Authorization": "Bearer token", "Content-Type": "application/json"},
            "body": '{"q": 1}',
            "error_type": "SignatureMismatch",
            "response": {
                "status": 403,
                "headers": {"X-Trace": "abc"},
                "body_summary": "forbidden",
            },
        }
        failure.update(overrides)
        record(failure, path)
        with open(path, encoding="utf-8") as handle:
            return json.loads(handle.readline())

    def test_record_keeps_full_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            sample = self._write_sample(path)
        self.assertEqual(sample["method"], "POST")
        self.assertEqual(sample["url"], "http://site/api")
        self.assertEqual(sample["headers"]["Authorization"], "Bearer token")
        self.assertEqual(sample["body"], '{"q": 1}')
        self.assertEqual(sample["error_type"], "SignatureMismatch")
        self.assertEqual(sample["response"]["status"], 403)
        self.assertEqual(sample["response"]["headers"]["X-Trace"], "abc")
        self.assertEqual(sample["response"]["body_summary"], "forbidden")
        self.assertTrue(sample["id"])
        self.assertTrue(sample["recorded_at"])

    def test_replay_rebuilds_request(self):
        seen = []

        def fetch(request):
            seen.append(request)
            return None

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            db = os.path.join(tmp, "drill.sqlite3")
            self._write_sample(path)
            replay([path], fetch, drill_db=db)
        self.assertEqual(
            seen[0],
            {
                "method": "POST",
                "url": "http://site/api",
                "headers": {
                    "Authorization": "Bearer token",
                    "Content-Type": "application/json",
                },
                "body": '{"q": 1}',
            },
        )

    def test_three_category_counts(self):
        def fetch(request):
            url = request["url"]
            if url.endswith("/fixed"):
                return None
            if url.endswith("/changed"):
                return "TimeoutError"
            return "SignatureMismatch"

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            db = os.path.join(tmp, "drill.sqlite3")
            for suffix in ("fixed", "changed", "same"):
                self._write_sample(path, url="http://site/" + suffix)
            counts = replay([path], fetch, drill_db=db)
        self.assertEqual(
            counts,
            {"fixed": 1, "still_failed": 1, "error_changed": 1},
        )

    def test_exception_counts_as_error_changed_or_still_failed(self):
        class Boom(Exception):
            pass

        def fetch(request):
            raise Boom()

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            db = os.path.join(tmp, "drill.sqlite3")
            self._write_sample(path, error_type="Boom")
            counts = replay([path], fetch, drill_db=db)
        self.assertEqual(counts, {"fixed": 0, "still_failed": 1, "error_changed": 0})

    def test_repeated_replay_does_not_duplicate_rows(self):
        def fetch(request):
            return None

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            db = os.path.join(tmp, "drill.sqlite3")
            self._write_sample(path)
            first = replay([path], fetch, drill_db=db)
            second = replay([path], fetch, drill_db=db)
            connection = sqlite3.connect(db)
            rows = connection.execute(
                "SELECT sample_id, replay_count FROM replay_results"
            ).fetchall()
            connection.close()
        self.assertEqual(first, {"fixed": 1, "still_failed": 0, "error_changed": 0})
        self.assertEqual(second, {"fixed": 1, "still_failed": 0, "error_changed": 0})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 2)

    def test_drill_db_is_isolated_from_sample_and_other_db(self):
        def fetch(request):
            return None

        with tempfile.TemporaryDirectory() as tmp:
            sample_path = os.path.join(tmp, "failures.jsonl")
            drill_one = os.path.join(tmp, "one.sqlite3")
            drill_two = os.path.join(tmp, "two.sqlite3")
            self._write_sample(sample_path)
            replay([sample_path], fetch, drill_db=drill_one)
            replay([sample_path], fetch, drill_db=drill_two)
            with open(sample_path, encoding="utf-8") as handle:
                sample_lines = [line for line in handle.read().splitlines() if line]
            one = sqlite3.connect(drill_one)
            two = sqlite3.connect(drill_two)
            one_rows = one.execute("SELECT COUNT(*) FROM replay_results").fetchone()[0]
            two_rows = two.execute("SELECT COUNT(*) FROM replay_results").fetchone()[0]
            one.close()
            two.close()
        self.assertEqual(len(sample_lines), 1)
        self.assertEqual(one_rows, 1)
        self.assertEqual(two_rows, 1)

    def test_legacy_url_only_sample_still_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.jsonl")
            db = os.path.join(tmp, "drill.sqlite3")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"url": "http://legacy/ok"}) + "\n")
            counts = replay([path], lambda url: url.endswith("ok"), drill_db=db)
        self.assertEqual(counts, {"fixed": 1, "still_failed": 0, "error_changed": 0})
