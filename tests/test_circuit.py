import time
import unittest

from proxycircuit import PROXIES, acquire, report
from proxycircuit import circuit


class CircuitTest(unittest.TestCase):
    def setUp(self):
        circuit.reset()
        circuit.FAILURE_THRESHOLD = 3
        circuit.COOLDOWN_SECONDS = 30.0
        self.clock = [1000.0]
        circuit._now = lambda: self.clock[0]

    def tearDown(self):
        circuit._now = time.monotonic
        circuit.reset()

    def fail(self, site, proxy, times):
        for _ in range(times):
            report(site, proxy, False)

    def open_proxy(self, site, proxy):
        self.fail(site, proxy, circuit.FAILURE_THRESHOLD)

    def advance(self, seconds):
        self.clock[0] += seconds

    def test_acquire_returns_first_proxy(self):
        self.assertEqual(acquire("shop"), PROXIES[0])

    def test_threshold(self):
        self.fail("shop", PROXIES[0], circuit.FAILURE_THRESHOLD - 1)
        self.assertEqual(acquire("shop"), PROXIES[0])
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.CLOSED)
        report("shop", PROXIES[0], False)
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.OPEN)
        self.assertEqual(acquire("shop"), PROXIES[1])

    def test_success_resets_consecutive_failures(self):
        self.fail("shop", PROXIES[0], circuit.FAILURE_THRESHOLD - 1)
        report("shop", PROXIES[0], True)
        self.fail("shop", PROXIES[0], circuit.FAILURE_THRESHOLD - 1)
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.CLOSED)

    def test_cooldown(self):
        self.open_proxy("shop", PROXIES[0])
        self.advance(circuit.COOLDOWN_SECONDS - 1)
        self.assertEqual(acquire("shop"), PROXIES[1])
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.OPEN)
        self.advance(1)
        self.assertEqual(acquire("shop"), PROXIES[0])
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.HALF_OPEN)

    def test_half_open_single_probe(self):
        self.open_proxy("shop", PROXIES[0])
        self.advance(circuit.COOLDOWN_SECONDS)
        self.assertEqual(acquire("shop"), PROXIES[0])
        self.assertEqual(acquire("shop"), PROXIES[1])
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.HALF_OPEN)

    def test_probe_success_recovers(self):
        self.open_proxy("shop", PROXIES[0])
        self.advance(circuit.COOLDOWN_SECONDS)
        self.assertEqual(acquire("shop"), PROXIES[0])
        report("shop", PROXIES[0], True)
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.CLOSED)
        self.assertEqual(acquire("shop"), PROXIES[0])
        self.assertEqual(circuit.probe_results(), [("shop", PROXIES[0], True)])

    def test_probe_failure_retimes_cooldown(self):
        self.open_proxy("shop", PROXIES[0])
        self.advance(circuit.COOLDOWN_SECONDS)
        self.assertEqual(acquire("shop"), PROXIES[0])
        report("shop", PROXIES[0], False)
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.OPEN)
        self.advance(circuit.COOLDOWN_SECONDS - 1)
        self.assertEqual(acquire("shop"), PROXIES[1])
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.OPEN)
        self.advance(1)
        self.assertEqual(acquire("shop"), PROXIES[0])
        self.assertEqual(circuit.state_of("shop", PROXIES[0]), circuit.HALF_OPEN)
        self.assertEqual(circuit.probe_results(), [("shop", PROXIES[0], False)])

    def test_site_isolation(self):
        self.open_proxy("shop", PROXIES[0])
        self.assertEqual(acquire("shop"), PROXIES[1])
        self.assertEqual(acquire("news"), PROXIES[0])
        self.assertEqual(circuit.state_of("news", PROXIES[0]), circuit.CLOSED)

    def test_all_open_raises_for_that_site_only(self):
        for proxy in PROXIES:
            self.open_proxy("shop", proxy)
        with self.assertRaises(RuntimeError):
            acquire("shop")
        self.assertEqual(acquire("news"), PROXIES[0])

    def test_transitions_and_state_counts(self):
        self.open_proxy("shop", PROXIES[0])
        self.advance(circuit.COOLDOWN_SECONDS)
        acquire("shop")
        report("shop", PROXIES[0], True)
        kinds = [(old, new) for _, _, old, new, _ in circuit.transitions()]
        self.assertEqual(
            kinds,
            [
                (circuit.CLOSED, circuit.OPEN),
                (circuit.OPEN, circuit.HALF_OPEN),
                (circuit.HALF_OPEN, circuit.CLOSED),
            ],
        )
        counts = circuit.state_counts()
        self.assertEqual(counts[circuit.OPEN], 0)
        self.assertEqual(counts[circuit.HALF_OPEN], 0)
        self.assertEqual(counts[circuit.CLOSED], 1)

    def test_invalid_arguments_raise(self):
        with self.assertRaises(ValueError):
            acquire("")
        with self.assertRaises(ValueError):
            report("shop", "9.9.9.9:1", False)
        with self.assertRaises(TypeError):
            report("shop", PROXIES[0], "ok")

    def test_report_on_open_raises(self):
        self.open_proxy("shop", PROXIES[0])
        with self.assertRaises(RuntimeError):
            report("shop", PROXIES[0], False)


if __name__ == "__main__":
    unittest.main()
