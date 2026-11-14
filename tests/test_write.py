import unittest

from idemwrite import reset, write


class WriteTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_later_overwrites(self):
        write([{"key": "k", "v": 2}])
        write([{"key": "k", "v": 1}])
        self.assertEqual(write([{"key": "k", "v": 1}])["rows"]["k"]["v"], 1)

    def test_idem_key_ignored(self):
        first = write([{"key": "k", "v": 1}], idem_key="x")
        second = write([{"key": "k", "v": 1}], idem_key="x")
        self.assertEqual(first["written"], second["written"])

    def test_no_version(self):
        write([{"key": "k", "v": 1}])
        self.assertNotIn("version", write([{"key": "k", "v": 1}])["rows"]["k"])
