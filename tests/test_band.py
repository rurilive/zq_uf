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
