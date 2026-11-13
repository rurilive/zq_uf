import time
import unittest
from multiprocessing import get_context

from crawlbudget import (
    LIMIT,
    BudgetError,
    configure,
    consume,
    remaining,
    reset,
    stats,
)


class BudgetTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_consume_counts(self):
        self.assertEqual(consume("shop", 3), 3)
        self.assertEqual(remaining("shop"), LIMIT - 3)

    def test_sites_share_counter(self):
        consume("shop", 10)
        self.assertEqual(remaining("news"), LIMIT - 10)

    def test_over_limit_raises(self):
        consume("shop", LIMIT)
        with self.assertRaises(RuntimeError):
            consume("news", 1)


def _worker(attempts):
    ok = 0
    for _ in range(attempts):
        if isinstance(consume("site", 1), int):
            ok += 1
    return ok


class SiteBudgetTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_site_allocation(self):
        configure("shop", daily=10)
        configure("news", daily=5)
        self.assertEqual(consume("shop", 10), 10)
        self.assertEqual(remaining("shop"), 0)
        self.assertEqual(remaining("news"), 5)
        wait = consume("shop", 1)
        self.assertIsInstance(wait, float)
        self.assertGreater(wait, 0)
        self.assertEqual(remaining("news"), 5)

    def test_over_limit_returns_wait_and_counts_reject(self):
        configure("shop", daily=3)
        consume("shop", 3)
        wait = consume("shop", 1)
        self.assertIsInstance(wait, float)
        self.assertGreater(wait, 0)
        row = stats("shop")["sites"]["shop"]
        self.assertEqual(row["spent"], 3)
        self.assertEqual(row["remaining"], 0)
        self.assertEqual(row["rejected"], 1)

    def test_burst_wait_then_refill(self):
        configure("shop", daily=100, burst=2, window=0.2)
        self.assertEqual(consume("shop", 1), 1)
        self.assertEqual(consume("shop", 1), 2)
        wait = consume("shop", 1)
        self.assertIsInstance(wait, float)
        self.assertGreater(wait, 0)
        time.sleep(wait + 0.05)
        self.assertEqual(consume("shop", 1), 3)

    def test_invalid_args_raise(self):
        with self.assertRaises(ValueError):
            consume("", 1)
        with self.assertRaises(ValueError):
            consume("shop", 0)
        with self.assertRaises(BudgetError):
            consume("shop", LIMIT + 1)
        with self.assertRaises(ValueError):
            configure("shop", daily=0)

    def test_multiprocess_shared_counter(self):
        configure("site", daily=100)
        try:
            ctx = get_context("fork")
        except ValueError:
            self.skipTest("fork start method unavailable")
        with ctx.Pool(4) as pool:
            oks = pool.map(_worker, [30] * 4)
        self.assertEqual(sum(oks), 100)
        self.assertEqual(remaining("site"), 0)
        self.assertEqual(stats("site")["sites"]["site"]["rejected"], 20)
