import unittest

from ratemesh import close, configure, give_back, stats, take
from ratemesh import mesh


class MeshTest(unittest.TestCase):
    def setUp(self):
        mesh._used.clear()

    def test_parent_counter_shared(self):
        take("p", "a", 2)
        take("p", "b", 3)
        self.assertEqual(mesh._used["p"], 5)

    def test_no_borrow_between_children(self):
        take("p", "a", 2)
        self.assertNotIn("a", mesh._used)

    def test_give_back_is_global(self):
        take("p", "a", 2)
        self.assertEqual(give_back("a", 1)["p"], 1)


class MeshGridTest(unittest.TestCase):
    def setUp(self):
        mesh._reset()

    def tearDown(self):
        mesh._reset()

    def test_weighted_allocation(self):
        configure("wp", quota=12, weights={"a": 3, "b": 1})
        take("wp", "a", 9)
        take("wp", "b", 3)
        s = stats("wp")
        self.assertEqual(s["children"]["a"]["allotment"], 9)
        self.assertEqual(s["children"]["b"]["allotment"], 3)
        self.assertEqual(mesh._used["wp"], 12)
        take("wp", "a", 5)  # 父总额已满，拒绝
        self.assertEqual(mesh._used["wp"], 12)

    def test_borrow_and_return(self):
        configure("bp", quota=10, weights={"a": 1, "b": 1}, ttl=100)
        take("bp", "a", 5)
        take("bp", "a", 3)  # 自有用尽，向空闲的 b 借 3
        s = stats("bp")
        self.assertEqual(s["children"]["a"]["borrowed_in"], 3)
        self.assertEqual(s["children"]["b"]["lent_out"], 3)
        give_back("a", 3)  # 先还借用
        s = stats("bp")
        self.assertEqual(s["children"]["a"]["borrowed_in"], 0)
        self.assertEqual(s["children"]["b"]["lent_out"], 0)
        self.assertEqual(mesh._used["bp"], 5)

    def test_borrow_cap(self):
        configure("cp", quota=10, weights={"a": 1, "b": 1},
                  borrow_cap=2, ttl=100)
        take("cp", "a", 5)
        take("cp", "a", 5)  # 借用上限 2，其余拒绝
        s = stats("cp")
        self.assertEqual(s["children"]["a"]["borrowed_in"], 2)
        self.assertEqual(mesh._used["cp"], 7)

    def test_reclaim_expiry_idempotent(self):
        clock = [0.0]
        original = mesh._now
        mesh._now = lambda: clock[0]
        try:
            configure("ep", quota=10, weights={"a": 1, "b": 1}, ttl=10)
            take("ep", "a", 5)
            take("ep", "a", 4)  # 借 4，t=10 到期
            self.assertEqual(mesh._used["ep"], 9)
            clock[0] = 11.0
            s = stats("ep")
            self.assertEqual(s["children"]["a"]["borrowed_in"], 0)
            self.assertEqual(s["children"]["b"]["lent_out"], 0)
            self.assertEqual(s["reclaimed"], 1)
            self.assertEqual(mesh._used["ep"], 5)
            s = stats("ep")  # 重复回收是空操作
            self.assertEqual(s["reclaimed"], 1)
            self.assertEqual(mesh._used["ep"], 5)
        finally:
            mesh._now = original

    def test_give_back_idempotent(self):
        configure("gp", quota=10, weights={"a": 1, "b": 1}, ttl=100)
        take("gp", "a", 4)
        give_back("a", 4)
        give_back("a", 4)      # 重复还：空操作
        give_back("a", 100)    # 多还：空操作
        give_back("ghost", 3)  # 未知子任务：空操作
        self.assertEqual(mesh._used["gp"], 0)
        self.assertEqual(stats("gp")["children"]["a"]["used_own"], 0)

    def test_parent_close_reclaims_all(self):
        configure("xp", quota=10, weights={"a": 1, "b": 1}, ttl=1000)
        take("xp", "a", 5)
        take("xp", "a", 3)  # 借 3
        self.assertEqual(close("xp"), 1)
        self.assertNotIn("xp", mesh._used)
        self.assertEqual(stats("xp")["children"], {})
        self.assertEqual(close("xp"), 0)  # 重复关闭：幂等
        give_back("a", 1)  # 子额度已回收，归还是空操作
        self.assertNotIn("xp", mesh._used)
