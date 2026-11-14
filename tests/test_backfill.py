import unittest

from backfillplan import Job, plan, run


class RecordingUpstream:
    def __init__(self):
        self.calls = []

    def fetch(self, shard, checkpoint, limit):
        self.calls.append((shard["id"], checkpoint))
        start = shard["start"] if checkpoint is None else checkpoint + 1
        return list(range(start, min(shard["end"], start + limit)))


class ShardingTest(unittest.TestCase):
    def test_key_shards_cover_range(self):
        shards = plan({"start": 1, "end": 101}, 4)["shards"]
        self.assertEqual(len(shards), 4)
        self.assertEqual(shards[0]["start"], 1)
        self.assertEqual(shards[-1]["end"], 101)
        for prev, nxt in zip(shards, shards[1:]):
            self.assertEqual(prev["end"], nxt["start"])
        self.assertEqual(len({s["id"] for s in shards}), 4)
        self.assertTrue(all(s["deps"] == [] for s in shards))
        prios = [s["priority"] for s in shards]
        self.assertEqual(prios, sorted(prios, reverse=True))

    def test_time_buckets_and_deps(self):
        p = plan({"start": 1, "end": 101,
                  "time_start": 0, "time_end": 7200, "time_step": 3600}, 2)
        shards = p["shards"]
        self.assertEqual(len(shards), 4)
        first_bucket = [s["id"] for s in shards if s["time_start"] == 0]
        second_bucket = [s for s in shards if s["time_start"] == 3600]
        self.assertEqual(len(first_bucket), 2)
        self.assertEqual(len(second_bucket), 2)
        for s in second_bucket:
            self.assertEqual(sorted(s["deps"]), sorted(first_bucket))

    def test_tiny_range_merges(self):
        self.assertEqual(len(plan({"start": 1, "end": 4}, 8)["shards"]), 1)


class DependencyOrderTest(unittest.TestCase):
    def test_later_bucket_waits_for_earlier(self):
        upstream = RecordingUpstream()
        p = plan({"start": 1, "end": 41,
                  "time_start": 0, "time_end": 7200, "time_step": 3600}, 2)
        summary = run(p, 4, upstream=upstream)
        self.assertEqual(summary["remaining"], 0)
        seen = []
        for shard_id, _ in upstream.calls:
            if shard_id not in seen:
                seen.append(shard_id)
        self.assertLess(seen.index("shard-0-1"), seen.index("shard-1-0"))
        self.assertLess(seen.index("shard-0-0"), seen.index("shard-1-0"))


class DynamicConcurrencyTest(unittest.TestCase):
    def test_idle_upstream_ramps_up_to_cap(self):
        class Idle(RecordingUpstream):
            def pressure(self):
                return 0.0

        summary = run(plan({"start": 1, "end": 201}, 8), 4, upstream=Idle())
        self.assertEqual(summary["workers"], 4)
        self.assertTrue(summary["concurrency"])
        self.assertEqual(summary["concurrency"][-1]["to"], 4)

    def test_busy_upstream_backs_off(self):
        class Busy(RecordingUpstream):
            def pressure(self):
                return 1.0

        summary = run(plan({"start": 1, "end": 201}, 8), 4, upstream=Busy())
        self.assertTrue(summary["concurrency"])
        self.assertEqual(summary["concurrency"][-1]["to"], 1)
        self.assertEqual(summary["remaining"], 0)

    def test_workers_capped_by_shard_count(self):
        summary = run(plan({"start": 1, "end": 21}, 2), 8)
        self.assertEqual(summary["workers"], 2)


class CheckpointRetryTest(unittest.TestCase):
    def test_failed_shard_resumes_from_checkpoint(self):
        class Flaky(RecordingUpstream):
            def __init__(self):
                super().__init__()
                self.failed = False

            def fetch(self, shard, checkpoint, limit):
                self.calls.append((shard["id"], checkpoint))
                if checkpoint == 20 and not self.failed:
                    self.failed = True
                    raise RuntimeError("upstream 500")
                start = shard["start"] if checkpoint is None else checkpoint + 1
                return list(range(start, min(shard["end"], start + limit)))

        upstream = Flaky()
        sink = {}
        summary = run(plan({"start": 1, "end": 41}, 1), 1,
                      upstream=upstream, sink=sink, batch_size=10)
        self.assertEqual(summary["done"], 1)
        self.assertEqual(summary["failed"], 0)
        shard = summary["shards"][0]
        self.assertEqual(shard["retries"], 1)
        resume_calls = [c for c in upstream.calls if c[1] == 20]
        self.assertEqual(len(resume_calls), 2)
        self.assertEqual(sorted(sink), list(range(1, 41)))
        self.assertEqual(summary["duplicates"], 0)

    def test_shard_gives_up_after_max_retries(self):
        class Down(RecordingUpstream):
            def fetch(self, shard, checkpoint, limit):
                raise RuntimeError("down")

        summary = run(plan({"start": 1, "end": 41}, 1), 1,
                      upstream=Down(), max_retries=2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["shards"][0]["retries"], 3)


class IdempotencyTest(unittest.TestCase):
    def test_rerun_skips_done_shards(self):
        sink, state = {}, {}
        p = plan({"start": 1, "end": 41}, 2)
        first = run(p, 2, sink=sink, state=state)
        self.assertEqual(first["written"], 40)
        second = run(p, 2, sink=sink, state=state)
        self.assertEqual(second["written"], 0)
        self.assertEqual(len(sink), 40)
        self.assertTrue(all(s["status"] == "skipped"
                            for s in second["shards"]))

    def test_duplicate_keys_deduped(self):
        class Repeating(RecordingUpstream):
            def fetch(self, shard, checkpoint, limit):
                keys = super().fetch(shard, checkpoint, limit)
                return keys + keys[:1]

        summary = run(plan({"start": 1, "end": 11}, 1), 1,
                      upstream=Repeating())
        self.assertEqual(summary["written"], 10)
        self.assertGreater(summary["duplicates"], 0)


class PauseResumeTest(unittest.TestCase):
    def test_pause_query_resume(self):
        job = Job(plan({"start": 1, "end": 41}, 4), 4,
                  after_shard=self._pause_once())
        summary = job.run()
        self.assertTrue(summary["paused"])
        self.assertGreater(summary["remaining"], 0)
        self.assertTrue(job.status()["paused"])
        job.resume()
        summary = job.run()
        self.assertFalse(summary["paused"])
        self.assertEqual(summary["remaining"], 0)
        self.assertEqual(summary["done"], 4)

    @staticmethod
    def _pause_once():
        fired = []

        def hook(job, shard):
            if not fired:
                fired.append(True)
                job.pause()

        return hook


if __name__ == "__main__":
    unittest.main()
