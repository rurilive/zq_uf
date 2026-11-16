import unittest
from datetime import datetime, timedelta

from irplaybook import (
    open,
    reset,
    timeline,
    run,
    confirm_leftover,
    close,
    stats,
    set_clock,
)


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += timedelta(seconds=seconds)
        return self.now


class PlaybookTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.clock = FakeClock(datetime(2026, 11, 17, 9, 0, 0))
        set_clock(self.clock)

    def test_open_returns_step_count(self):
        out = open({"id": "e1", "type": "ransomware"})
        self.assertEqual(out["steps"], 4)
        self.assertEqual(out["done"], 0)
        self.assertEqual(out["state"], "open")

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            open({"id": "e2", "type": "ddos"})

    def test_duplicate_open_raises(self):
        open({"id": "e3", "type": "phishing"})
        with self.assertRaises(ValueError):
            open({"id": "e3", "type": "phishing"})

    def test_steps_keep_template_order_with_preconditions(self):
        open({"id": "e4", "type": "ransomware"})
        names = [s["name"] for s in timeline("e4")["steps"]]
        self.assertEqual(names, ["isolate", "collect", "cleanup", "restore"])

    def test_step_requires_predecessors(self):
        # 取证/清理不得越过前置步骤，避免破坏证据
        open({"id": "e5", "type": "ransomware"})
        with self.assertRaises(ValueError):
            run("e5", "collect", "li", "done")
        run("e5", "isolate", "li", "done")
        run("e5", "collect", "li", "done")
        states = {s["name"]: s["state"] for s in timeline("e5")["steps"]}
        self.assertEqual(states["isolate"], "done")
        self.assertEqual(states["collect"], "done")

    def test_forensic_step_precedes_cleanup(self):
        open({"id": "e6", "type": "ransomware"})
        run("e6", "isolate", "li", "done")
        with self.assertRaises(ValueError):
            run("e6", "cleanup", "wang", "done")
        cleanup = next(s for s in timeline("e6")["steps"] if s["name"] == "cleanup")
        self.assertTrue(cleanup["blocked"])
        collect = next(s for s in timeline("e6")["steps"] if s["name"] == "collect")
        self.assertTrue(collect["forensic"])
        self.assertFalse(cleanup["forensic"])

    def test_run_records_operator_time_and_result(self):
        open({"id": "e7", "type": "phishing", "operator": "li"})
        self.clock.tick(5)
        run("e7", "quarantine", "li", "done", note="mailbox isolated")
        out = timeline("e7")
        step_records = [r for r in out["records"] if r["kind"] == "step"]
        self.assertEqual(len(step_records), 1)
        record = step_records[0]
        self.assertEqual(record["step"], "quarantine")
        self.assertEqual(record["operator"], "li")
        self.assertEqual(record["result"], "done")
        self.assertEqual(record["note"], "mailbox isolated")
        self.assertEqual(record["at"], "2026-11-17T09:00:05")

    def test_failed_step_blocks_successors_and_registers_leftover(self):
        open({"id": "e8", "type": "ransomware"})
        run("e8", "isolate", "li", "done")
        run("e8", "collect", "li", "failed", note="disk read error")

        view = timeline("e8")
        cleanup = next(s for s in view["steps"] if s["name"] == "cleanup")
        self.assertTrue(cleanup["blocked"])
        self.assertFalse(cleanup["runnable"])
        collect = next(s for s in view["steps"] if s["name"] == "collect")
        self.assertEqual(collect["state"], "failed")

        self.assertEqual(len(view["leftover"]), 1)
        leftover = view["leftover"][0]
        self.assertEqual(leftover["step"], "collect")
        self.assertEqual(leftover["reason"], "failed")
        self.assertEqual(leftover["operator"], "li")
        self.assertFalse(leftover["confirmed"])

        with self.assertRaises(ValueError):
            run("e8", "cleanup", "wang", "done")

    def test_timeout_blocks_and_retry_resolves_leftover(self):
        open({"id": "e9", "type": "ransomware"})
        run("e9", "isolate", "li", "done")
        run("e9", "collect", "li", "timeout", note="agent hung")
        self.assertEqual(timeline("e9")["leftover"][0]["reason"], "timeout")

        self.clock.tick(10)
        run("e9", "collect", "wang", "done", note="rerun ok")
        view = timeline("e9")
        self.assertEqual(view["leftover"][0]["resolved"], "retried")
        self.assertTrue(view["leftover"][0]["resolved_at"])
        cleanup = next(s for s in view["steps"] if s["name"] == "cleanup")
        self.assertTrue(cleanup["runnable"])

    def test_records_sorted_by_time_with_different_operators(self):
        open({"id": "e10", "type": "intrusion", "operator": "li"})
        run("e10", "isolate", "li", "done")
        run("e10", "collect", "wang", "done")
        out = timeline("e10")
        steps = [r for r in out["records"] if r["kind"] == "step"]
        self.assertEqual([(r["step"], r["operator"]) for r in steps],
                         [("isolate", "li"), ("collect", "wang")])
        step = next(s for s in out["steps"] if s["name"] == "collect")
        self.assertEqual(step["operator"], "wang")
        self.assertEqual(step["operators"], ["wang"])

    def test_parallel_steps_are_marked_and_not_blocked_by_peers(self):
        open({"id": "e11", "type": "phishing"})
        run("e11", "quarantine", "li", "done")
        view = timeline("e11")
        collect = next(s for s in view["steps"] if s["name"] == "collect")
        notify = next(s for s in view["steps"] if s["name"] == "notify")
        self.assertEqual(collect["parallel_with"], ["notify"])
        self.assertEqual(notify["parallel_with"], ["collect"])
        self.assertEqual(collect["group"], "g1")
        self.assertTrue(collect["runnable"])
        self.assertTrue(notify["runnable"])

        # 并行对端失败不阻断本组另一步骤
        run("e11", "collect", "li", "failed")
        notify_view = next(s for s in timeline("e11")["steps"]
                           if s["name"] == "notify")
        self.assertTrue(notify_view["runnable"])
        run("e11", "notify", "wang", "done")

    def test_closable_gates_on_required_steps(self):
        open({"id": "e12", "type": "ransomware"})
        self.assertFalse(timeline("e12")["closable"])
        run("e12", "isolate", "li", "done")
        run("e12", "collect", "li", "done")
        run("e12", "cleanup", "li", "done")
        self.assertFalse(timeline("e12")["closable"])
        run("e12", "restore", "li", "done")
        self.assertTrue(timeline("e12")["closable"])

    def test_close_blocked_mentions_missing_items(self):
        open({"id": "e13", "type": "ransomware"})
        run("e13", "isolate", "li", "done")
        with self.assertRaises(ValueError) as ctx:
            close("e13", "li")
        self.assertIn("incomplete steps", str(ctx.exception))

    def test_leftover_must_be_confirmed_before_close_optional_step(self):
        open({"id": "e14", "type": "intrusion"})
        for step in ["isolate", "collect", "cleanup", "review"]:
            run("e14", step, "li", "done")
        run("e14", "harden", "li", "failed", note="change window missed")

        view = timeline("e14")
        self.assertFalse(view["closable"])
        check = {c["step"]: c for c in view["close_check"]}
        self.assertFalse(check["harden"]["satisfied"])
        self.assertEqual(check["harden"]["required"], False)

        with self.assertRaises(ValueError):
            close("e14", "li")
        confirm_leftover("e14", "harden", "zhang", "deferred to next sprint")
        self.assertTrue(timeline("e14")["closable"])

        result = close("e14", "zhang")
        self.assertEqual(result["state"], "closed")
        self.assertTrue(result["closed_at"])
        close_record = result["records"][-1]
        self.assertEqual(close_record["kind"], "close")
        self.assertEqual(close_record["operator"], "zhang")

    def test_confirmed_leftover_on_required_step_still_blocks_close(self):
        open({"id": "e15", "type": "phishing"})
        run("e15", "quarantine", "li", "done")
        run("e15", "collect", "li", "failed")
        run("e15", "notify", "li", "done")
        confirm_leftover("e15", "collect", "zhang", "accepted risk")
        # 遗留项已确认，但必做项未完成，仍不可关闭
        self.assertFalse(timeline("e15")["closable"])
        with self.assertRaises(ValueError):
            close("e15", "zhang")
        # 补做成功后才放行
        run("e15", "collect", "wang", "done")
        self.assertTrue(timeline("e15")["closable"])

    def test_no_step_runs_after_close(self):
        open({"id": "e16", "type": "phishing"})
        for step in ["quarantine", "collect", "notify"]:
            run("e16", step, "li", "done")
        close("e16", "li")
        with self.assertRaises(ValueError):
            run("e16", "notify", "li", "done")
        with self.assertRaises(ValueError):
            close("e16", "li")

    def test_stats_only_closed_events_grouped_by_type(self):
        open({"id": "c1", "type": "phishing"})
        for step in ["quarantine", "collect", "notify"]:
            run("c1", step, "li", "done")
        self.clock.tick(60)
        close("c1", "li")

        open({"id": "c2", "type": "phishing"})
        for step in ["quarantine", "collect", "notify"]:
            run("c2", step, "wang", "done")
        self.clock.tick(30)
        close("c2", "wang")

        open({"id": "c3", "type": "ransomware"})
        run("c3", "isolate", "li", "done")  # 未关闭，不计入

        summary = stats()
        self.assertEqual(set(summary), {"phishing"})
        self.assertEqual(summary["phishing"]["count"], 2)
        self.assertEqual(summary["phishing"]["total_seconds"], 90.0)
        self.assertEqual(summary["phishing"]["min_seconds"], 30.0)
        self.assertEqual(summary["phishing"]["max_seconds"], 60.0)
        self.assertEqual(stats("phishing")["count"], 2)
        self.assertEqual(stats("ransomware"), {})

    def test_ransomware_full_flow_for_review_material(self):
        open({"id": "r1", "type": "ransomware", "operator": "li"})
        run("r1", "isolate", "li", "done")
        run("r1", "collect", "wang", "done", note="memory image captured")
        run("r1", "cleanup", "wang", "done")
        run("r1", "restore", "zhao", "done")
        close("r1", "zhao")

        out = timeline("r1")
        kinds = [r["kind"] for r in out["records"]]
        self.assertEqual(kinds[0], "open")
        self.assertEqual(kinds[-1], "close")
        self.assertEqual(len([r for r in out["records"] if r["kind"] == "step"]), 4)
        self.assertTrue(all(c["satisfied"] for c in out["close_check"]))
        self.assertEqual(out["leftover"], [])


if __name__ == "__main__":
    unittest.main()
