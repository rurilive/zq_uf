import unittest

from coldhot import archive, query


class TierTest(unittest.TestCase):
    def test_archive_counts(self):
        self.assertGreaterEqual(archive("t1", {"start": 1, "end": 2})["archived"], 1)

    def test_query_only_online(self):
        self.assertEqual(query("t1", {})["routed"], "online")

    def test_no_location(self):
        self.assertIsNone(archive("t2", {})["location"])
