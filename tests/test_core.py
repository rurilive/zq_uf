import unittest

from idemwrite import upsert


class IdemwriteTest(unittest.TestCase):
    def test_insert(self):
        store = upsert([{"id": 1, "v": "a"}], ["id"])
        self.assertEqual(store[(1,)], {"id": 1, "v": "a"})

    def test_overwrite(self):
        store = upsert([{"id": 1, "v": "a"}], ["id"])
        upsert([{"id": 1, "v": "b"}], ["id"], store)
        self.assertEqual(store[(1,)]["v"], "b")

    def test_composite_key(self):
        store = upsert([{"a": 1, "b": 2}], ["a", "b"])
        self.assertIn((1, 2), store)
