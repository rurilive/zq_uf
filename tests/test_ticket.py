import unittest

import capticket
from capticket import TicketSystem
from capticket import ticket as ticket_mod


def make_system():
    clock = {"t": 1000.0}
    return TicketSystem(now=lambda: clock["t"], timeout=30, cooldown=60), clock


class TicketTest(unittest.TestCase):
    def test_submit_returns_position_and_occupancy(self):
        sys, _ = make_system()
        r1 = sys.submit("a", {"url": "https://x/1", "code": "1"})
        self.assertEqual(r1["status"], "active")
        self.assertEqual(r1["position"], 0)
        r2 = sys.submit("a", {"url": "https://x/2"})
        self.assertEqual(r2["status"], "queued")
        self.assertEqual(r2["position"], 1)
        self.assertTrue(r2["account_busy"])

    def test_account_mutex_one_active(self):
        sys, _ = make_system()
        sys.submit("a", {"url": "https://x/1"})
        sys.submit("a", {"url": "https://x/2"})
        view = sys.account_view("a")
        self.assertEqual(view["status"], "busy")
        self.assertEqual(len(view["queued"]), 1)

    def test_priority_dispatched_first(self):
        sys, _ = make_system()
        sys.submit("a", {"url": "https://x/0", "code": "0"})
        low = sys.submit("a", {"url": "https://x/1", "priority": 1})
        high = sys.submit("a", {"url": "https://x/2", "priority": 9})
        sys.resolve(1, "0")
        view = sys.account_view("a")
        self.assertEqual(view["current"]["ticket"], high["ticket"])
        self.assertEqual(view["queued"][0]["ticket"], low["ticket"])

    def test_adjust_priority_by_duty(self):
        sys, _ = make_system()
        sys.submit("a", {"url": "https://x/0", "code": "0"})
        low = sys.submit("a", {"url": "https://x/1", "priority": 1})
        sys.submit("a", {"url": "https://x/2", "priority": 2})
        self.assertFalse(sys.adjust_priority(1, 5)["adjusted"])  # 活动工单不可调
        r = sys.adjust_priority(low["ticket"], 99, by="duty-01")
        self.assertTrue(r["adjusted"])
        self.assertEqual(r["position"], 1)
        sys.resolve(1, "0")
        self.assertEqual(sys.account_view("a")["current"]["ticket"], low["ticket"])

    def test_timeout_releases_account_and_records(self):
        sys, clock = make_system()
        sys.submit("a", {"url": "https://x/1"})
        clock["t"] += 31
        view = sys.account_view("a")
        self.assertEqual(view["status"], "idle")
        self.assertEqual(len(view["timeouts"]), 1)
        self.assertEqual(view["timeouts"][0]["ticket"], 1)
        self.assertEqual(sys.ticket_status(1)["status"], "timeout")

    def test_cooldown_per_site_and_auto_resume(self):
        sys, clock = make_system()
        sys.submit("a", {"url": "https://x/1", "site": "shop"})
        clock["t"] += 31  # 超时，shop 站点冷却 60s
        r = sys.submit("a", {"url": "https://x/2", "site": "shop"})
        self.assertEqual(r["status"], "queued")      # 冷却中不接单
        r2 = sys.submit("a", {"url": "https://y/1", "site": "mall"})
        self.assertEqual(r2["status"], "active")     # 其他站点不受影响
        sys.resolve(r2["ticket"], "anything")        # 无期望码，任意回写
        clock["t"] += 61                             # 冷却结束
        view = sys.account_view("a")
        self.assertEqual(view["cooldowns"], {})
        self.assertEqual(view["current"]["site"], "shop")  # 自动恢复接单

    def test_resolve_confirmed_once(self):
        sys, _ = make_system()
        sys.submit("a", {"url": "https://x/1", "code": "1234"})
        first = sys.resolve(1, "1234")
        self.assertTrue(first["resume"])
        again = sys.resolve(1, "1234")
        self.assertFalse(again["resume"])
        self.assertTrue(again["duplicated"])

    def test_resolve_mismatch_rejected(self):
        sys, _ = make_system()
        sys.submit("a", {"url": "https://x/1", "code": "1234"})
        bad = sys.resolve(1, "0000")
        self.assertFalse(bad["resolved"])
        self.assertEqual(bad["reason"], "code_mismatch")
        self.assertEqual(sys.ticket_status(1)["status"], "active")
        self.assertTrue(sys.resolve(1, "1234")["resolved"])

    def test_reassign_timeout_ticket(self):
        sys, clock = make_system()
        sys.submit("a", {"url": "https://x/1"})
        clock["t"] += 31
        sys.account_view("a")  # 触发超时回收
        r = sys.reassign(1, account="b")
        self.assertTrue(r["reassigned"])
        self.assertEqual(sys.ticket_status(1)["account"], "b")
        self.assertFalse(sys.reassign(1)["reassigned"])  # 已不在超时态
        self.assertEqual(len(sys.account_view("a")["reassigns"]), 1)

    def test_manual_cooldown_adjust(self):
        sys, clock = make_system()
        sys.submit("a", {"url": "https://x/1", "site": "shop"})
        clock["t"] += 31
        sys.account_view("a")
        r = sys.set_cooldown("a", "shop", 0)  # 值班同学手动解除
        self.assertIsNone(r["cooldown_until"])
        self.assertEqual(sys.account_view("a")["cooldowns"], {})

    def test_export_and_replay(self):
        sys, clock = make_system()
        sys.submit("a", {"url": "https://x/1", "code": "1"})
        sys.submit("a", {"url": "https://x/2", "priority": 9})
        sys.resolve(1, "1")
        clock["t"] += 31
        sys.submit("b", {"url": "https://y/1"})
        data = sys.export()
        clone = TicketSystem.replay(data["events"])
        again = clone.export()
        self.assertEqual([t["status"] for t in data["tickets"]],
                         [t["status"] for t in again["tickets"]])
        self.assertEqual(len(data["timeouts"]), len(again["timeouts"]))
        self.assertEqual(len(data["events"]), len(again["events"]))

    def test_module_level_api(self):
        ticket_mod.reset(now=lambda: 0.0)
        r = capticket.submit("a", {"url": "https://x", "priority": 9})
        self.assertEqual(r["priority"], 9)
        self.assertFalse(capticket.resolve(999, "0000")["resume"])


if __name__ == "__main__":
    unittest.main()
