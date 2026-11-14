import unittest
from unittest import mock

from rowversion import (
    RowVersionError,
    configure,
    get,
    manual_queue,
    reset,
    update,
)


class VersionTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_update_requires_matching_version(self):
        result = update("k", {"v": 1}, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], 1)
        stale = update("k", {"v": 2}, 0)
        self.assertFalse(stale["ok"])
        self.assertEqual(get("k"), {"row": {"v": 1}, "version": 1})
        ok = update("k", {"v": 2}, 1)
        self.assertTrue(ok["ok"])
        self.assertEqual(get("k")["row"]["v"], 2)

    def test_conflict_carries_current_and_version(self):
        update("k", {"v": 1}, 0)
        conflict = update("k", {"v": 2}, 0)
        self.assertTrue(conflict["conflict"])
        self.assertEqual(conflict["current"], {"v": 1})
        self.assertEqual(conflict["version"], 1)
        self.assertEqual(conflict["retries"], 0)

    def test_merge_retry_succeeds(self):
        update("k", {"n": 1}, 0)
        snap = get("k")
        update("k", {"n": 2}, snap["version"])  # 另一方先提交
        result = update(
            "k",
            {"n": snap["row"]["n"] + 10},
            snap["version"],
            merge=lambda current, mine: {"n": current["n"] + 10},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["retries"], 1)
        self.assertEqual(get("k")["row"]["n"], 12)

    def _bumping_merge(self, current, mine):
        # 模拟并发方：每次合并前又把版本推高，迫使重试一直冲突
        update("k", {"n": current["n"] + 1}, get("k")["version"])
        return {"n": current["n"] + 10}

    def test_retry_exhausted_goes_manual(self):
        configure(max_retries=2, backoff=0)
        update("k", {"n": 1}, 0)
        snap = get("k")
        update("k", {"n": 2}, snap["version"])  # 先制造首次冲突
        result = update("k", {"n": 5}, snap["version"], merge=self._bumping_merge)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "retry_exhausted")
        self.assertEqual(result["attempts"], 2)
        queue = manual_queue()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["key"], "k")
        self.assertEqual(queue[0]["row"], {"n": 5})
        self.assertEqual(queue[0]["version"], snap["version"])
        self.assertEqual(queue[0]["attempts"], 2)
        self.assertEqual(queue[0]["reason"], "retry_exhausted")

    def test_backoff_sleeps_with_exponential_delay(self):
        configure(max_retries=2, backoff=0.1)
        update("k", {"n": 1}, 0)
        snap = get("k")
        update("k", {"n": 2}, snap["version"])
        with mock.patch("rowversion.version.time.sleep") as sleep:
            update("k", {"n": 5}, snap["version"], merge=self._bumping_merge)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.1, 0.2])

    def test_delete_requires_matching_version(self):
        update("k", {"v": 1}, 0)
        stale = update("k", None, 0)
        self.assertTrue(stale["conflict"])
        self.assertIsNotNone(get("k"))
        deleted = update("k", None, 1)
        self.assertTrue(deleted["ok"])
        self.assertTrue(deleted["deleted"])
        self.assertIsNone(get("k"))

    def test_stale_version_cannot_revive_deleted_row(self):
        update("k", {"v": 1}, 0)
        update("k", None, 1)
        revive = update("k", {"v": 2}, 1)  # 删除前的旧版本
        self.assertTrue(revive["conflict"])
        self.assertTrue(revive["deleted"])
        self.assertIsNone(get("k"))
        ok = update("k", {"v": 2}, 2)  # 用墓碑版本重建
        self.assertTrue(ok["ok"])
        self.assertEqual(get("k")["row"], {"v": 2})

    def test_delete_missing_key_returns_not_found(self):
        result = update("nope", None, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_found")

    def test_invalid_args_raise_clear_error(self):
        with self.assertRaises(RowVersionError):
            update("k", {"v": 1}, -1)
        with self.assertRaises(RowVersionError):
            update("k", {"v": 1}, 1.5)
        with self.assertRaises(RowVersionError):
            update("k", "not-a-dict", 0)
        with self.assertRaises(RowVersionError):
            update("", {"v": 1}, 0)
        with self.assertRaises(RowVersionError):
            update("k", {"v": 1}, 0, merge="not-callable")
        with self.assertRaises(RowVersionError):
            configure(max_retries=-1)

    def test_merge_returning_non_dict_raises(self):
        update("k", {"n": 1}, 0)
        update("k", {"n": 2}, 1)
        with self.assertRaises(RowVersionError):
            update("k", {"n": 3}, 1, merge=lambda cur, mine: "bad")


if __name__ == "__main__":
    unittest.main()
