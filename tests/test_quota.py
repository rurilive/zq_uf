import unittest

from crawlquota import CAPS, acquire, release, usage


class QuotaTest(unittest.TestCase):
    def setUp(self):
        for kind in CAPS:
            release("t", kind, CAPS[kind])

    def test_acquire_increments(self):
        self.assertEqual(acquire("a", "thread", 2), 2)

    def test_over_cap_raises(self):
        acquire("a", "thread", CAPS["thread"])
        with self.assertRaises(RuntimeError):
            acquire("b", "thread", 1)

    def test_tasks_share_usage(self):
        acquire("a", "fd", 5)
        self.assertEqual(usage()["fd"], 5)
        release("a", "fd", 5)
