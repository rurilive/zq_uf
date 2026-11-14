import time
import unittest

from formcaptcha import resolve, submit
from formcaptcha import ticket


class TicketTest(unittest.TestCase):
    def setUp(self):
        ticket._queue.clear(); ticket._seq = 0

    def test_submit_returns_id(self):
        self.assertTrue(submit("acc-1", "login")["id"].startswith("T-"))

    def test_queue_order(self):
        submit("a", "t1"); submit("b", "t2")
        self.assertEqual(ticket._queue[0]["account"], "a")

    def test_resolve_ok(self):
        self.assertTrue(resolve("T-1", "8f3k")["ok"])


class _ClockedBase(unittest.TestCase):
    """可控时钟 + 全量状态清理，避免影响既有 TicketTest。"""

    def setUp(self):
        ticket._queue.clear(); ticket._expired.clear(); ticket._resolved.clear()
        ticket._seq = 0
        self.now = [1000.0]
        ticket._clock = lambda: self.now[0]

    def tearDown(self):
        ticket._clock = time.monotonic
        ticket._queue.clear(); ticket._expired.clear(); ticket._resolved.clear()
        ticket._seq = 0


class PriorityDispatchTest(_ClockedBase):
    def test_high_priority_jumps_queue(self):
        submit("a", "t1")
        submit("b", "t2")
        submit("c", "urgent", priority=10)
        self.assertEqual([t["id"] for t in ticket._queue], ["T-3", "T-1", "T-2"])

    def test_same_priority_keeps_fifo(self):
        submit("a", "t1", priority=5)
        submit("b", "t2", priority=5)
        self.assertEqual(ticket._queue[0]["account"], "a")

    def test_bad_arguments_raise(self):
        with self.assertRaises(ticket.TicketError):
            submit("", "t1")
        with self.assertRaises(ticket.TicketError):
            submit("a", "t1", priority="high")
        with self.assertRaises(ticket.TicketError):
            submit("a", "t1", timeout=0)


class AccountMutexTest(_ClockedBase):
    def test_same_account_second_ticket_rejected(self):
        submit("acc-1", "login")
        with self.assertRaises(ticket.AccountBusyError):
            submit("acc-1", "again")

    def test_account_released_after_resolve(self):
        t = submit("acc-1", "login")
        resolve(t["id"], "8f3k")
        self.assertEqual(submit("acc-1", "again")["id"], "T-2")


class TimeoutReclaimTest(_ClockedBase):
    def test_expired_ticket_reclaimed_and_account_released(self):
        submit("acc-1", "login", timeout=30)
        self.now[0] += 31
        t2 = submit("acc-1", "retry")  # 账号已释放，不再互斥
        self.assertEqual(t2["id"], "T-2")
        self.assertEqual([r["id"] for r in ticket._expired], ["T-1"])
        self.assertEqual([t["id"] for t in ticket._queue], ["T-2"])

    def test_resolve_expired_ticket_raises(self):
        submit("acc-1", "login", timeout=30)
        self.now[0] += 31
        with self.assertRaises(ticket.TicketStateError):
            resolve("T-1", "8f3k")


class ResolveConfirmTest(_ClockedBase):
    def test_resolve_confirms_only_once(self):
        submit("acc-1", "login")
        self.assertTrue(resolve("T-1", "8f3k")["ok"])
        with self.assertRaises(ticket.TicketStateError):
            resolve("T-1", "8f3k")
        self.assertEqual(len(ticket._resolved), 1)

    def test_unknown_ticket_recorded_as_manual(self):
        self.assertTrue(resolve("T-99", "abcd")["ok"])
        self.assertEqual(ticket._resolved[0]["source"], "manual")
