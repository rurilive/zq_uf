import unittest

from proxycircuit import PROXIES, acquire, report
from proxycircuit import circuit


class CircuitTest(unittest.TestCase):
    def setUp(self):
        circuit._disabled = False

    def test_acquire_returns_first_proxy(self):
        self.assertEqual(acquire("shop"), PROXIES[0])

    def test_one_failure_disables_all_sites(self):
        report("shop", PROXIES[0], False)
        with self.assertRaises(RuntimeError):
            acquire("news")

    def test_success_does_not_restore(self):
        report("shop", PROXIES[0], False)
        report("news", PROXIES[1], True)
        with self.assertRaises(RuntimeError):
            acquire("shop")
