import unittest

from slapredict import predict, reset


class PredictTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_returns_average(self):
        self.assertEqual(predict("etl", 1000)["eta"], 210)

    def test_same_for_every_task(self):
        self.assertEqual(predict("etl", 10), predict("report", 100000))

    def test_no_interval(self):
        result = predict("etl", 10)
        self.assertEqual(result["low"], result["high"])
