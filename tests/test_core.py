import unittest
from datetime import datetime, timezone

from aggretract import Aggregator


def day(day_of_month):
    return int(
        datetime(2026, 1, day_of_month, tzinfo=timezone.utc).timestamp()
    )


class AggretractTest(unittest.TestCase):
    def test_apply_adds(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "value": 2}])
        agg.apply([{"key": "a", "value": 3}])
        self.assertEqual(agg.query("a"), 5)

    def test_unknown_key_is_zero(self):
        self.assertEqual(Aggregator().query("x"), 0)

    def test_multiple_keys(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "value": 1}, {"key": "b", "value": 2}])
        self.assertEqual(agg.totals, {"a": 1, "b": 2})

    def test_same_id_new_version_overwrites(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "id": "r1", "version": 1, "value": 10}])
        agg.apply([{"key": "a", "id": "r1", "version": 2, "value": 4}])
        self.assertEqual(agg.query("a"), 4)
        self.assertEqual(agg.stats("a")["detail_count"], 1)

    def test_stale_version_is_ignored(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "id": "r1", "version": 2, "value": 4}])
        agg.apply([{"key": "a", "id": "r1", "version": 1, "value": 99}])
        self.assertEqual(agg.query("a"), 4)
        self.assertEqual(agg.stats("a")["detail_count"], 1)

    def test_replay_same_batch_is_idempotent(self):
        agg = Aggregator()
        batch = [{"key": "a", "id": "r1", "version": 1, "value": 7}]
        agg.apply(batch)
        agg.apply(batch)
        self.assertEqual(agg.query("a"), 7)
        self.assertEqual(agg.stats("a")["detail_count"], 1)

    def test_retract_subtracts_value(self):
        agg = Aggregator()
        agg.apply([
            {"key": "a", "id": "r1", "version": 1, "value": 10},
            {"key": "a", "id": "r2", "version": 1, "value": 5},
        ])
        agg.apply([{"key": "a", "id": "r1", "version": 2, "op": "retract"}])
        self.assertEqual(agg.query("a"), 5)
        stats = agg.stats("a")
        self.assertEqual(stats["detail_count"], 1)
        self.assertEqual(stats["retracted_count"], 1)

    def test_retract_unknown_id_never_negative(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "id": "ghost", "version": 1, "op": "retract"}])
        stats = agg.stats("a")
        self.assertEqual(agg.query("a"), 0)
        self.assertEqual(stats["detail_count"], 0)
        self.assertEqual(stats["retracted_count"], 1)

    def test_replay_retract_never_double_counts(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "id": "r1", "version": 1, "value": 10}])
        retract = [{"key": "a", "id": "r1", "version": 2, "op": "retract"}]
        agg.apply(retract)
        agg.apply(retract)
        stats = agg.stats("a")
        self.assertEqual(agg.query("a"), 0)
        self.assertEqual(stats["detail_count"], 0)
        self.assertEqual(stats["retracted_count"], 1)

    def test_higher_version_upsert_revives_retracted_record(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "id": "r1", "version": 1, "value": 7}])
        agg.apply([{"key": "a", "id": "r1", "version": 2, "op": "retract"}])
        agg.apply([{"key": "a", "id": "r1", "version": 3, "value": 11}])
        self.assertEqual(agg.query("a"), 11)
        self.assertEqual(agg.stats("a")["detail_count"], 1)

    def test_late_event_within_lateness_uses_its_window(self):
        agg = Aggregator(allowed_lateness=60)
        agg.apply([{"key": "a", "id": "r1", "version": 1, "value": 5,
                    "event_time": day(2) + 120}])
        agg.apply([{"key": "a", "id": "r2", "version": 1, "value": 3,
                    "event_time": day(2) + 60}])
        self.assertEqual(agg.query("a"), 8)
        self.assertEqual(
            agg.query("a", window=Aggregator.window_for(day(2))), 8
        )
        self.assertEqual(agg.stats("a")["late_count"], 0)

    def test_late_event_beyond_lateness_is_dropped_and_counted(self):
        agg = Aggregator(allowed_lateness=60)
        agg.apply([{"key": "a", "id": "r1", "version": 1, "value": 5,
                    "event_time": day(2)}])
        agg.apply([{"key": "a", "id": "r0", "version": 1, "value": 9,
                    "event_time": day(1)}])
        self.assertEqual(agg.query("a"), 5)
        self.assertEqual(agg.stats("a")["late_count"], 1)
        self.assertEqual(agg.stats("a")["detail_count"], 1)

    def test_late_update_replaces_old_contribution(self):
        agg = Aggregator(allowed_lateness=60)
        agg.apply([{"key": "a", "id": "r1", "version": 1, "value": 10,
                    "event_time": day(2)}])
        agg.apply([{"key": "a", "id": "r1", "version": 2, "value": 2,
                    "event_time": day(1)}])
        self.assertEqual(agg.query("a"), 0)
        stats = agg.stats("a")
        self.assertEqual(stats["detail_count"], 0)
        self.assertEqual(stats["late_count"], 1)

    def test_reconciliation_totals_detail_and_late_counts(self):
        agg = Aggregator(allowed_lateness=60)
        agg.apply([
            {"key": "a", "id": "r1", "version": 1, "value": 5,
             "event_time": day(2)},
            {"key": "a", "id": "r2", "version": 1, "value": 3,
             "event_time": day(2)},
            {"key": "a", "id": "r3", "version": 1, "value": 9,
             "event_time": day(1)},
        ])
        stats = agg.stats("a")
        self.assertEqual(stats["total"], 8)
        self.assertEqual(stats["detail_count"], 2)
        self.assertEqual(stats["late_count"], 1)
        window_totals = sum(
            bucket["total"] for bucket in stats["windows"].values()
        )
        self.assertEqual(window_totals, stats["total"])
        self.assertEqual(agg.stats()["total"], 8)

    def test_iso_event_time_matches_epoch_window(self):
        epoch = day(3)
        iso = datetime.fromtimestamp(epoch, timezone.utc).isoformat()
        self.assertEqual(
            Aggregator.window_for(iso), Aggregator.window_for(epoch)
        )
