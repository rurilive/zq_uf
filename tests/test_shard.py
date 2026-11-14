import unittest

from crawlshard import elect, write
from crawlshard import shard


class ShardTest(unittest.TestCase):
    def setUp(self):
        shard._owners.clear()

    def test_elect_records_owner(self):
        self.assertEqual(elect("s1", "n1")["node"], "n1")

    def test_second_elect_overwrites(self):
        elect("s1", "n1")
        self.assertEqual(elect("s1", "n2")["term"], 0)

    def test_non_owner_can_write(self):
        elect("s1", "n1")
        self.assertTrue(write("s1", "n2", {"id": 1})["written"])
