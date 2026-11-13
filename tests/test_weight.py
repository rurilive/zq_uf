import unittest

from siteweight import SITES, feedback, next_site, reset


class WeightTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_round_robin(self):
        picked = [next_site() for _ in range(6)]
        self.assertEqual(picked, SITES * 2)

    def test_feedback_does_not_reorder(self):
        feedback("shop", False)
        feedback("shop", False)
        self.assertEqual(next_site(), "shop")

    def test_all_sites_equal(self):
        counts = {site: 0 for site in SITES}
        for _ in range(30):
            counts[next_site()] += 1
        self.assertEqual(set(counts.values()), {10})
