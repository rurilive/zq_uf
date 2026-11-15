import unittest

from capticket import (
    TicketError,
    dispatch,
    reassign,
    resolve,
    status,
    submit,
    sweep,
)


class TicketTest(unittest.TestCase):
    def test_submit_queues(self):
        self.assertGreaterEqual(submit("a", {"url": "https://x"})["queued"], 1)

    def test_resolve_ends(self):
        self.assertFalse(resolve(1, "1234")["resume"])

    def test_priority_recorded_only(self):
        self.assertEqual(submit("b", {"priority": 9})["priority"], 9)


BASE = 1_000_000.0  # 固定时钟，避免真实等待


class WorkflowTest(unittest.TestCase):
    """优先级分派、账号互斥、超时回收、回写确认；账号名 wf-* 与上面隔离。"""

    def test_high_priority_dispatched_first(self):
        submit("wf-a", {"priority": 1}, now=BASE)
        submit("wf-b", {"priority": 9}, now=BASE)
        self.assertEqual(dispatch(now=BASE)["account"], "wf-b")

    def test_account_mutex(self):
        submit("wf-c", {}, now=BASE)
        with self.assertRaises(TicketError):
            submit("wf-c", {}, now=BASE)

    def test_timeout_reclaim_releases_account(self):
        t = submit("wf-d", {"timeout": 5, "cooldown": 0}, now=BASE)
        expired = sweep(now=BASE + 10)
        self.assertIn(t["ticket"], [e["ticket"] for e in expired])
        # 账号已释放，可再提交
        submit("wf-d", {}, now=BASE + 10)

    def test_resolve_confirms_once(self):
        t = submit("wf-e", {}, now=BASE)
        self.assertTrue(resolve(t["ticket"], "1234", now=BASE)["confirmed"])
        with self.assertRaises(TicketError):
            resolve(t["ticket"], "1234", now=BASE)

    def test_reassign_keeps_history_and_resumes(self):
        t = submit("wf-f", {"timeout": 5, "cooldown": 0}, now=BASE)
        sweep(now=BASE + 10)
        new = reassign(t["ticket"], now=BASE + 10)
        self.assertEqual(new["parent"], t["ticket"])
        events = [h["event"] for h in new["history"]]
        self.assertIn("submitted", events)  # 原工单历史保留
        self.assertIn("expired", events)
        self.assertTrue(resolve(new["ticket"], "9999", now=BASE + 11)["resume"])

    def test_expired_ticket_cannot_resolve(self):
        t = submit("wf-g", {"timeout": 5, "cooldown": 0}, now=BASE)
        sweep(now=BASE + 10)
        with self.assertRaises(TicketError):
            resolve(t["ticket"], "1234", now=BASE + 10)

    def test_reconcile_mismatch_rejected_then_corrected(self):
        t = submit("wf-h", {"expect": "4321"}, now=BASE)
        with self.assertRaises(TicketError):
            resolve(t["ticket"], "0000", now=BASE)
        # 对不上账不消耗工单，改正后可回写
        self.assertTrue(resolve(t["ticket"], "4321", now=BASE)["resolved"])

    def test_cooldown_is_per_site(self):
        t = submit("wf-i", {"site": "s1", "cooldown": 60}, now=BASE)
        resolve(t["ticket"], "1", now=BASE)
        with self.assertRaises(TicketError):
            submit("wf-i", {"site": "s1"}, now=BASE + 1)
        # 换站点不受该冷却限制
        submit("wf-i", {"site": "s2"}, now=BASE + 1)

    def test_unknown_ticket_error(self):
        with self.assertRaises(TicketError):
            resolve(999999, "1", now=BASE)

    def test_status_outputs_queue_timeout_writeback(self):
        submit("wf-j", {"priority": 3}, now=BASE)
        t = submit("wf-k", {"timeout": 5, "cooldown": 0}, now=BASE)
        sweep(now=BASE + 10)
        new = reassign(t["ticket"], now=BASE + 10)
        resolve(new["ticket"], "77", now=BASE + 11)
        snap = status(now=BASE + 11)
        self.assertIn("wf-j", [q["account"] for q in snap["queue"]])
        self.assertIn(t["ticket"], [e["ticket"] for e in snap["expired"]])
        self.assertEqual(snap["writebacks"][-1]["code"], "77")
