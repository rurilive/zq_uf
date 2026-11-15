import importlib
import json
import os
import tempfile
import unittest

from partclone import clone, verify

_impl = importlib.import_module("partclone.clone")


class CloneTest(unittest.TestCase):
    def test_clone_copies_rows(self):
        self.assertEqual(clone({"tag": "t1", "rows": 5})["copied"], 5)

    def test_clone_has_no_snapshot(self):
        self.assertIsNone(clone({"tag": "t2", "rows": 1})["snapshot"])

    def test_verify_always_matches(self):
        clone({"tag": "t3", "rows": 2})
        self.assertTrue(verify("t3")["matched"])


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="partclone-test-")
        _impl._configure(self.state)

    def _paths(self, tag):
        return _impl._paths(tag)


class SnapshotCloneTest(StateTestCase):
    def test_snapshot_records_version_and_rows(self):
        first = clone({"tag": "s1", "data": [{"a": 1}, {"a": 2}]})
        self.assertEqual(first["snapshot"]["version"], 1)
        self.assertEqual(first["snapshot"]["rows"], 2)
        second = clone({"tag": "s1", "data": [{"a": 1}, {"a": 2}]})
        self.assertEqual(second["snapshot"]["version"], 2)

    def test_source_readonly_during_clone(self):
        source = {"rows": [1, 2, 3], "readonly": False}
        seen = []
        clone({
            "tag": "s2", "data": [1, 2, 3], "source": source,
            "on_copy": lambda n: seen.append(source["readonly"]),
        })
        self.assertTrue(seen)
        self.assertTrue(all(seen))
        self.assertFalse(source["readonly"])

    def test_target_visible_after_clone(self):
        result = clone({"tag": "s3", "data": [1, 2]})
        self.assertTrue(result["visible"])
        self.assertTrue(os.path.exists(self._paths("s3")["target"]))


class FailureCleanupTest(StateTestCase):
    def test_failure_cleans_partial_target(self):
        result = clone({
            "tag": "f1", "data": list(range(10)),
            "fail_at": 4, "fail_times": 2, "max_retries": 2,
        })
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts"], 2)
        self.assertTrue(result["cleaned"])
        self.assertFalse(os.path.exists(self._paths("f1")["target"]))
        self.assertFalse(verify("f1")["matched"])

    def test_retry_limit_is_enforced(self):
        result = clone({
            "tag": "f2", "data": list(range(5)),
            "fail_at": 1, "fail_times": 99, "max_retries": 3,
        })
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(result["status"], "failed")

    def test_aborted_on_checksum_mismatch_keeps_counts(self):
        def tamper(n):
            if n == 2:
                with open(self._paths("f3")["staging"], "a", encoding="utf-8") as fh:
                    fh.write(json.dumps("bad") + "\n")

        result = clone({"tag": "f3", "data": [1, 2, 3], "on_copy": tamper})
        self.assertEqual(result["status"], "aborted")
        self.assertEqual(result["source_rows"], 3)
        self.assertEqual(result["target_rows"], 4)
        self.assertFalse(result["visible"])
        self.assertFalse(os.path.exists(self._paths("f3")["target"]))


class ResumeTest(StateTestCase):
    def test_checkpoint_persisted_and_resumed(self):
        data = list(range(10))
        first = clone({
            "tag": "r1", "data": data,
            "fail_at": 4, "fail_times": 1, "max_retries": 1,
        })
        self.assertEqual(first["status"], "failed")
        with open(self._paths("r1")["checkpoint"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["copied"], 4)

        second = clone({"tag": "r1", "data": data})
        self.assertTrue(second["resumed"])
        self.assertEqual(second["copied"], 10)
        self.assertTrue(verify("r1")["matched"])

    def test_stale_checkpoint_discarded_when_snapshot_changes(self):
        clone({
            "tag": "r2", "data": [1] * 10,
            "fail_at": 3, "fail_times": 1, "max_retries": 1,
        })
        result = clone({"tag": "r2", "data": [2] * 6})
        self.assertFalse(result["resumed"])
        self.assertEqual(verify("r2")["rows"], 6)


class VerifyTest(StateTestCase):
    def test_verify_detects_tampered_target_and_keeps_counts(self):
        clone({"tag": "v1", "data": [1, 2, 3]})
        with open(self._paths("v1")["target"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps(99) + "\n")
        result = verify("v1")
        self.assertFalse(result["matched"])
        self.assertEqual(result["source_rows"], 3)
        self.assertEqual(result["target_rows"], 4)

    def test_verify_writes_audit(self):
        clone({"tag": "v2", "data": [1]})
        verify("v2")
        with open(os.path.join(self.state, "audit.log"), encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertTrue(
            any(l.get("event") == "verify" and l.get("tag") == "v2"
                and l.get("matched") for l in lines)
        )


if __name__ == "__main__":
    unittest.main()
