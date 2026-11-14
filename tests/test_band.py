import unittest

from crawlband import give_back, take
from crawlband import band


class BandTest(unittest.TestCase):
    def setUp(self):
        band._used = 0

    def test_take_returns_amount(self):
        self.assertEqual(take("t1", 100), 100)

    def test_give_back_frees(self):
        take("t1", 100)
        self.assertEqual(give_back("t1", 50), 50)

    def test_over_rate_raises(self):
        take("t1", band.RATE)
        with self.assertRaises(RuntimeError):
            take("t2", 1)


class PerTaskBandTest(unittest.TestCase):
    def setUp(self):
        band.reset()
        self._cfg = dict(band.CONFIG)
        self._now = band._now
        band.CONFIG.update(base_rate=100, burst_limit=150,
                           borrow_limit=100, borrow_ttl=10.0, groups={})

    def tearDown(self):
        band.CONFIG.clear()
        band.CONFIG.update(self._cfg)
        band._now = self._now
        band.reset()

    def test_per_task_base_limit(self):
        band.CONFIG.update(burst_limit=100, borrow_limit=0)
        self.assertEqual(take("a", 100), 100)
        with self.assertRaises(RuntimeError):
            take("a", 1)

    def test_burst_cap(self):
        take("b", 10)  # b 空闲 90，可借出
        self.assertEqual(take("a", 150), 150)  # 自有 100 + 借 50
        with self.assertRaises(RuntimeError):
            take("a", 1)  # 已到突发上限 150

    def test_borrow_and_give_back(self):
        take("b", 10)
        take("a", 150)
        s = band.stats()
        self.assertEqual(s["a"]["borrowed_in"], 50)
        self.assertEqual(s["b"]["lent_out"], 50)
        give_back("a", 50)  # 先还借入
        s = band.stats()
        self.assertEqual(s["a"]["borrowed_in"], 0)
        self.assertEqual(s["b"]["lent_out"], 0)
        self.assertEqual(s["a"]["rate"], 100)

    def test_borrow_expires(self):
        clock = [1000.0]
        band._now = lambda: clock[0]
        take("b", 10)
        take("a", 150)  # 借 50，10 秒后到期
        clock[0] += 11.0
        s = band.stats()  # 触发到期回收
        self.assertEqual(s["a"]["borrowed_in"], 0)
        self.assertEqual(s["b"]["lent_out"], 0)
        self.assertEqual(s["a"]["rate"], 100)

    def test_finish_returns_all(self):
        take("b", 10)
        take("a", 150)
        band.finish("a")
        self.assertEqual(band._used, 10)
        self.assertNotIn("a", band.stats())
        band.finish("a")  # 可重入，不报错
        self.assertEqual(band._used, 10)

    def test_rejected_counted(self):
        band.CONFIG.update(burst_limit=100, borrow_limit=0)
        take("a", 100)
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                take("a", 1)
        self.assertEqual(band.stats()["a"]["rejected"], 3)

    def test_cross_group_cannot_borrow(self):
        band.CONFIG["groups"] = {"a": "g1", "b": "g2"}
        take("b", 10)  # b 在 g2，空闲额度借不到 g1
        take("a", 100)
        with self.assertRaises(RuntimeError):
            take("a", 1)  # 同组无人可借

    def test_give_back_reentrant(self):
        take("a", 80)
        give_back("a", 9999)  # 超还只清到 0
        self.assertEqual(band.stats()["a"]["rate"], 0)
        self.assertEqual(give_back("nobody", 10), band._used)
