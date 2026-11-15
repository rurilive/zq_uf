import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from seedscore import ReviewError, ReviewLog, replay
from seedscore.export import export_restores


class ReviewLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "restores.jsonl")

    def test_restore_requires_actor_and_reason(self):
        log = ReviewLog()
        with self.assertRaises(ReviewError):
            log.restore("https://a.example/", actor="", reason="x")
        with self.assertRaises(ReviewError):
            log.restore("https://a.example/", actor="alice", reason=" ")

    def test_restore_is_recorded_and_active(self):
        log = ReviewLog()
        [entry] = log.restore(
            "https://a.example/page", actor="alice", reason="false kill", score=0.2
        )
        self.assertEqual(entry["target"], "a.example/page")
        self.assertEqual(entry["action"], "restore")
        self.assertTrue(entry["id"])
        self.assertEqual(log.restored_targets(), ["a.example/page"])
        self.assertTrue(log.is_restored("https://a.example/page?utm_source=x"))

    def test_canonical_targets_match_filter_targets(self):
        log = ReviewLog()
        log.restore("https://www.A.example/page/?utm_source=x", actor="a", reason="r")
        self.assertEqual(log.restored_targets(), ["a.example/page"])

    def test_persistence_and_reload(self):
        log = ReviewLog(self.path)
        log.restore("https://a.example/", actor="alice", reason="false kill")
        log.restore("https://b.example/", actor="bob", reason="manual check")
        reloaded = ReviewLog(self.path)
        self.assertEqual(len(reloaded), 2)
        self.assertEqual(
            sorted(reloaded.restored_targets()), ["a.example/", "b.example/"]
        )
        # Replaying yields the same state.
        self.assertEqual(sorted(replay(self.path).restored_targets()), sorted(reloaded.restored_targets()))

    def test_revoke_cancels_restore(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="false kill")
        log.revoke("https://a.example/", actor="bob", reason="actually junk")
        self.assertEqual(log.restored_targets(), [])
        self.assertFalse(log.is_restored("https://a.example/"))

    def test_restore_after_revoke_is_active_again(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r1")
        log.revoke("https://a.example/", actor="bob", reason="r2")
        log.restore("https://a.example/", actor="alice", reason="r3")
        self.assertEqual(log.restored_targets(), ["a.example/"])
        self.assertEqual([e["action"] for e in log.entries], ["restore", "revoke", "restore"])

    def test_point_in_time_replay(self):
        log = ReviewLog()
        early = datetime(2026, 1, 1, tzinfo=timezone.utc)
        late = datetime(2026, 6, 1, tzinfo=timezone.utc)
        log.restore("https://a.example/", actor="alice", reason="r", at=early)
        log.restore("https://b.example/", actor="alice", reason="r", at=late)
        self.assertEqual(log.restored_targets(at=early), ["a.example/"])
        self.assertEqual(sorted(log.restored_targets(at=late)), ["a.example/", "b.example/"])
        self.assertEqual(log.restored_targets(at=datetime(2025, 1, 1, tzinfo=timezone.utc)), [])

    def test_query_by_actor_site_action_and_window(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r", at="2026-01-01T00:00:00Z")
        log.restore("https://b.example/", actor="bob", reason="r", at="2026-02-01T00:00:00Z")
        log.revoke("https://a.example/", actor="alice", reason="r", at="2026-03-01T00:00:00Z")

        self.assertEqual(len(log.query(actor="alice")), 2)
        self.assertEqual(len(log.query(action="restore")), 2)
        self.assertEqual(len(log.query(site="b.example")), 1)
        self.assertEqual(
            len(log.query(since="2026-01-15T00:00:00Z", until="2026-03-02T00:00:00Z")), 2
        )
        self.assertEqual(
            [entry["target"] for entry in log.query(include_revoked=False)], ["b.example/"]
        )

    def test_active_only_survives_revoke_then_restore(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r1")
        log.revoke("https://a.example/", actor="bob", reason="r2")
        log.restore("https://a.example/", actor="alice", reason="r3")
        active = log.query(include_revoked=False)
        self.assertEqual([entry["reason"] for entry in active], ["r3"])

    def test_query_by_target_accepts_url(self):
        log = ReviewLog()
        log.restore("https://a.example/page", actor="alice", reason="r")
        self.assertEqual(len(log.query(target="https://www.a.example/page/?utm_source=z")), 1)

    def test_export_jsonl_and_csv(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="false kill")
        text = log.export()
        self.assertEqual(len(text.strip().splitlines()), 1)
        self.assertEqual(json.loads(text)["actor"], "alice")
        csv_text = log.export(format="csv")
        self.assertIn("target", csv_text.splitlines()[0])
        self.assertIn("a.example", csv_text)

    def test_export_to_file_and_replay(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r")
        out = os.path.join(self.tmp.name, "export.csv")
        export_restores(log, out, format="csv")
        self.assertTrue(os.path.exists(out))
        with open(out, encoding="utf-8") as handle:
            self.assertIn("a.example", handle.read())

    def test_export_restores_with_query(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r")
        log.restore("https://b.example/", actor="bob", reason="r")
        text = export_restores(log, actor="bob")
        self.assertEqual(len(text.strip().splitlines()), 1)
        self.assertIn("b.example", text)

    def test_replay_until(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r", at="2026-01-01T00:00:00Z")
        log.restore("https://b.example/", actor="alice", reason="r", at="2026-06-01T00:00:00Z")
        out = os.path.join(self.tmp.name, "log.jsonl")
        log.export(out)
        partial = replay(out, until="2026-03-01T00:00:00Z")
        self.assertEqual(partial.restored_targets(), ["a.example/"])
        full = replay(out)
        self.assertEqual(len(full), 2)

    def test_replay_is_deterministic(self):
        log = ReviewLog(self.path)
        log.restore("https://a.example/", actor="alice", reason="r")
        first = replay(self.path).entries
        second = replay(self.path).entries
        self.assertEqual(first, second)

    def test_corrupt_log_is_rejected(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json}\n")
        with self.assertRaises(ReviewError):
            ReviewLog(self.path)

    def test_bad_action_rejected(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"action": "explode", "target": "a.example/"}) + "\n")
        with self.assertRaises(ReviewError):
            ReviewLog(self.path)

    def test_duplicate_entries_are_deduplicated(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r")
        entry = log.entries[0]
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
            handle.write(json.dumps(entry) + "\n")
        reloaded = ReviewLog(self.path)
        self.assertEqual(len(reloaded), 1)

    def test_summary(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r")
        log.revoke("https://a.example/", actor="bob", reason="r")
        summary = log.summary()
        self.assertEqual(summary["entries"], 2)
        self.assertEqual(summary["active"], 0)
        self.assertEqual(summary["actors"], {"alice": 1, "bob": 1})

    def test_same_second_events_get_distinct_ids(self):
        log = ReviewLog()
        log.restore("https://a.example/", actor="alice", reason="r", at="2026-01-01T00:00:00Z")
        log.restore("https://a.example/", actor="alice", reason="r", at="2026-01-01T00:00:00Z")
        ids = [entry["id"] for entry in log.entries]
        self.assertEqual(len(set(ids)), 2)


if __name__ == "__main__":
    unittest.main()
