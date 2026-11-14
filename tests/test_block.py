import unittest

from blockvote import allow, observe
from blockvote import block


class BlockTest(unittest.TestCase):
    def setUp(self):
        block.reset()
        self.clock = [0.0]
        block.set_clock(lambda: self.clock[0])

    def tearDown(self):
        block.set_clock()
        block.set_listener()

    def advance(self, seconds):
        self.clock[0] += seconds

    def test_403_blocks(self):
        self.assertTrue(observe("shop", "403"))

    def test_captcha_ignored(self):
        self.assertFalse(observe("shop", "captcha"))

    def test_no_recovery(self):
        observe("shop", "403")
        observe("shop", "200")
        self.assertFalse(allow("shop"))

    def test_multi_signal_voting(self):
        self.assertFalse(observe("shop", "redirect"))
        self.assertEqual(block.tally("shop"), 1)
        self.assertFalse(observe("shop", "ratelimit"))
        self.assertEqual(block.tally("shop"), 3)
        self.assertTrue(observe("shop", "empty"))
        self.assertEqual(block.tally("shop"), 5)
        self.assertEqual(block.state("shop"), block.OPEN)
        signals = [vote[2] for vote in block.votes("shop")]
        self.assertEqual(signals, ["redirect", "ratelimit", "empty"])

    def test_weak_signals_contradict_without_trip(self):
        observe("shop", "redirect")
        observe("shop", "empty")
        self.assertEqual(block.tally("shop"), 3)
        self.assertTrue(allow("shop"))
        self.assertEqual(block.state("shop"), block.CLOSED)

    def test_threshold_trip_emits_transition(self):
        transitions = []
        block.set_listener(
            lambda kind, site, payload: transitions.append((kind, payload))
            if kind == "transition" else None
        )
        observe("shop", "captcha")
        observe("shop", "ratelimit")
        self.assertEqual(block.state("shop"), block.OPEN)
        self.assertEqual(transitions[-1][1]["from"], block.CLOSED)
        self.assertEqual(transitions[-1][1]["to"], block.OPEN)
        self.assertEqual(transitions[-1][1]["reason"], "threshold")

    def test_vote_window_expires(self):
        observe("shop", "captcha")
        self.advance(block.VOTE_WINDOW + 1)
        self.assertEqual(block.tally("shop"), 0)
        observe("shop", "redirect")
        self.assertEqual(block.tally("shop"), 1)
        self.assertTrue(allow("shop"))

    def test_probe_recovery(self):
        observe("shop", "403")
        self.assertFalse(allow("shop"))
        self.advance(block.COOLDOWN)
        self.assertTrue(allow("shop"))
        self.assertEqual(block.state("shop"), block.HALF_OPEN)

        for streak in range(1, block.PROBE_SUCCESS_REQUIRED):
            self.assertTrue(observe("shop", "200"))
            self.assertFalse(allow("shop"))
            self.advance(block.PROBE_INTERVAL)

        self.assertEqual(
            sum(1 for probe in block.probes("shop") if probe["ok"]),
            block.PROBE_SUCCESS_REQUIRED - 1,
        )
        observe("shop", "200")
        self.assertEqual(block.state("shop"), block.CLOSED)
        self.assertTrue(allow("shop"))

    def test_probe_failure_reopens(self):
        observe("shop", "403")
        self.advance(block.COOLDOWN)
        observe("shop", "200")
        self.assertEqual(block.state("shop"), block.HALF_OPEN)
        observe("shop", "captcha")
        self.assertEqual(block.state("shop"), block.OPEN)
        self.assertFalse(allow("shop"))
        self.assertFalse(block.probes("shop")[-1]["ok"])

    def test_probes_are_low_frequency(self):
        observe("shop", "403")
        self.advance(block.COOLDOWN)
        observe("shop", "200")
        self.assertFalse(allow("shop"))
        observe("shop", "200")
        self.assertEqual(block.probes("shop")[-1]["streak"], 1)
        self.advance(block.PROBE_INTERVAL)
        self.assertTrue(allow("shop"))
        observe("shop", "200")
        self.assertEqual(block.probes("shop")[-1]["streak"], 2)

    def test_whitelist_overrides_trip(self):
        observe("shop", "403")
        self.assertFalse(allow("shop"))
        block.whitelist("shop", 100)
        self.assertTrue(allow("shop"))
        self.assertEqual(block.state("shop"), block.CLOSED)
        self.assertFalse(observe("shop", "403"))
        self.assertTrue(block.is_whitelisted("shop"))

    def test_whitelist_expires(self):
        observe("shop", "captcha")
        observe("shop", "ratelimit")
        block.whitelist("shop", 100)
        self.assertTrue(allow("shop"))
        self.advance(101)
        self.assertFalse(block.is_whitelisted("shop"))
        self.assertEqual(block.tally("shop"), 0)
        self.assertTrue(allow("shop"))
        observe("shop", "403")
        self.assertFalse(allow("shop"))
        self.assertEqual(block.state("shop"), block.OPEN)

    def test_events_log_votes_transitions_and_probes(self):
        observe("shop", "403")
        self.advance(block.COOLDOWN)
        observe("shop", "200")
        kinds = [event["type"] for event in block.events("shop")]
        self.assertIn("vote", kinds)
        self.assertIn("transition", kinds)
        self.assertIn("probe", kinds)
        vote_event = next(
            event for event in block.events("shop") if event["type"] == "vote"
        )
        self.assertEqual(vote_event["tally"], 4)
        self.assertEqual(vote_event["threshold"], block.TRIP_THRESHOLD)
