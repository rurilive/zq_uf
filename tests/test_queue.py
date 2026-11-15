import json
import unittest

from crawlprio2 import CrawlQueue, pop as global_pop, push as global_push


class FakeClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.q = CrawlQueue(clock=self.clock, starve_days=99)

    def test_round_robin_no_site_dominates_batch(self):
        for i in range(5):
            self.q.push({"id": f"b{i}", "site": "big", "priority": 1})
        for i in range(4):
            self.q.push({"id": f"s{i}", "site": "small", "priority": 1})

        result = self.q.pop(batch=4)
        sites = [it["site"] for it in result["items"]]
        self.assertEqual(sorted(sites), ["big", "big", "small", "small"])
        self.assertNotEqual(sites[0], sites[1])

        result = self.q.pop(batch=5)
        sites = [it["site"] for it in result["items"]]
        self.assertEqual(len(sites), 5)
        self.assertLessEqual(sites.count("big"), 3)
        # 两个站点都还有货时，不允许同一站点连占前两个名额
        self.assertNotEqual(sites[0], sites[1])

    def test_quota_exhausted_returns_wait_time(self):
        self.q.configure_site("default", "big", 2)
        for i in range(3):
            self.q.push({"id": f"b{i}", "site": "big"})

        result = self.q.pop(batch=10)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["blocked_sites"], ["big"])
        self.assertEqual(result["remaining_quota"]["big"], 0)
        self.assertEqual(result["retry_after_seconds"], 86400)

    def test_quota_refreshes_next_day(self):
        self.q.configure_site("default", "big", 1)
        for i in range(2):
            self.q.push({"id": f"b{i}", "site": "big"})
        self.assertEqual(len(self.q.pop(batch=5)["items"]), 1)

        self.clock.advance(86400)
        result = self.q.pop(batch=5)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(self.q.remaining_quota("default", "big"), 0)

    def test_high_priority_jumps_within_site_but_consumes_site_quota(self):
        self.q.push({"id": "low", "site": "a", "priority": 1})
        self.q.push({"id": "high", "site": "a", "priority": 9})

        result = self.q.pop(batch=1)
        self.assertEqual(result["items"][0]["id"], "high")
        # 高优没有独立通道，一样消耗本站名额
        self.assertEqual(result["remaining_quota"]["a"], 9999)

    def test_aging_boost_is_capped(self):
        self.q.push({"id": "old", "site": "s", "priority": 0})
        self.clock.advance(600)  # 等待 600s，速率 1/60 => 名义提权 10，上限 5
        self.q.push({"id": "fresh", "site": "s", "priority": 3})

        result = self.q.pop(batch=1)
        item = result["items"][0]
        self.assertEqual(item["id"], "old")
        self.assertEqual(item["boost"], 5.0)
        self.assertEqual(item["effective_priority"], 5.0)
        self.assertEqual(item["waited_seconds"], 600)

        uncapped_clock = FakeClock()
        uncapped = CrawlQueue(clock=uncapped_clock, max_boost=100,
                              starve_days=99)
        uncapped.push({"id": "old2", "site": "s", "priority": 0})
        uncapped_clock.advance(600)
        item = uncapped.pop(batch=1)["items"][0]
        self.assertEqual(item["boost"], 10.0)

    def test_release_requeues_with_seniority(self):
        self.q.push({"id": "x", "site": "s", "priority": 1})
        self.q.pop(batch=1)
        self.assertEqual(self.q.release("x")["attempt"], 2)

        self.clock.advance(120)
        item = self.q.pop(batch=1)["items"][0]
        self.assertEqual(item["id"], "x")
        self.assertEqual(item["attempt"], 2)
        # 保留首次入队时间，等待资历连续计算
        self.assertEqual(item["waited_seconds"], 120)

    def test_ack_removes_inflight(self):
        self.q.push({"id": "x", "site": "s"})
        self.q.pop(batch=1)
        self.assertTrue(self.q.ack("x")["acked"])
        self.assertFalse(self.q.ack("x")["acked"])

    def test_tenant_isolation(self):
        self.q.push({"id": "t1-only", "site": "s"}, tenant="t1")
        result = self.q.pop(tenant="t2")
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["retry_after_seconds"])

        self.q.configure_site("t1", "s", 1)
        self.assertEqual(self.q.remaining_quota("t1", "s"), 1)
        self.assertEqual(self.q.remaining_quota("t2", "s"), 10000)

    def test_records_query_export_replay(self):
        self.q.configure_site("default", "small", 0)
        self.q.push({"id": "b0", "site": "big"})
        self.q.push({"id": "s0", "site": "small"})
        self.q.pop(batch=5)

        rows = self.q.records(day=0)
        self.assertEqual([r["id"] for r in rows], ["b0"])
        self.assertEqual(self.q.records(site="small"), [])

        csv_text = self.q.export_records(day=0, fmt="csv")
        self.assertIn("effective_priority", csv_text)
        self.assertIn("waited_seconds", csv_text)
        json_text = self.q.export_records(fmt="json")
        self.assertEqual(json.loads(json_text)[0]["id"], "b0")

        self.q.ack("b0")
        replay = self.q.replay_day(0)
        self.assertEqual(replay["replayed"], ["b0"])
        self.clock.advance(86400)  # 次日再出队，避免同一天重复记录
        self.assertEqual(self.q.pop(batch=1)["items"][0]["id"], "b0")
        # 在途/在队 id 回放时跳过
        self.assertEqual(self.q.replay_day(0)["skipped"], ["b0"])

    def test_starvation_alert_for_small_site(self):
        q = CrawlQueue(clock=self.clock, starve_days=2)
        q.configure_site("default", "small", 0)
        for i in range(3):
            q.push({"id": f"b{i}", "site": "big"})
        q.push({"id": "s0", "site": "small"})

        self.assertEqual(q.starvation_alerts(), [])
        q.pop(batch=5)  # day 0
        self.clock.advance(86400)
        q.pop(batch=5)  # day 1，仍未达阈值
        self.assertEqual(q.starvation_alerts(), [])

        self.clock.advance(86400)
        alerts = q.pop(batch=5)["starvation_alerts"]  # day 2
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["site"], "small")
        self.assertEqual(alerts[0]["days_without_dequeue"], 2)
        self.assertIn("手动调大", alerts[0]["message"])

    def test_manual_quota_increase_takes_effect_same_day(self):
        self.q.configure_site("default", "big", 1)
        for i in range(2):
            self.q.push({"id": f"b{i}", "site": "big"})
        self.q.pop(batch=5)
        self.assertIsNotNone(self.q.pop(batch=5)["retry_after_seconds"])

        self.q.configure_site("default", "big", 5)
        result = self.q.pop(batch=5)
        self.assertEqual(len(result["items"]), 1)
        self.assertIsNone(result["retry_after_seconds"])

    def test_duplicate_id_rejected(self):
        self.q.push({"id": "dup", "site": "s"})
        self.q.pop(batch=1)
        # 在途期间重复入队被拒；ack 后同一 id 可再次入队
        self.assertTrue(self.q.push({"id": "dup", "site": "s"})["duplicate"])
        self.q.ack("dup")
        self.assertFalse(self.q.push({"id": "dup", "site": "s"})["duplicate"])

    def test_empty_pop(self):
        result = self.q.pop()
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["retry_after_seconds"])
        self.assertEqual(result["blocked_sites"], [])

    def test_module_level_compat_api(self):
        global_push({"id": "module-level-1", "site": "mod"})
        result = global_pop()
        self.assertEqual(result["items"][0]["id"], "module-level-1")
        self.assertIn("effective_priority", result["items"][0])
        self.assertIn("waited_seconds", result["items"][0])


if __name__ == "__main__":
    unittest.main()
