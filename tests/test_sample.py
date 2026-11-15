import unittest

from sampleci import error, sample


class SampleTest(unittest.TestCase):
    def test_size_follows_ratio(self):
        rows = [{"value": i} for i in range(100)]
        self.assertEqual(len(sample(rows, {"ratio": 0.2})["rows"]), 20)

    def test_no_seed(self):
        self.assertIsNone(sample([{"value": 1}], {"ratio": 1.0})["seed"])

    def test_no_range(self):
        self.assertIsNone(error({"rows": [{"value": 1}]})["range"])
