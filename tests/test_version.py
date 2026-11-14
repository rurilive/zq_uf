import unittest

from schemaversion import register, resolve
from schemaversion import version as module


class VersionTest(unittest.TestCase):
    def setUp(self):
        module._current = {"version": 0, "fields": {}}

    def test_register_sets_version(self):
        self.assertEqual(register({"version": 1, "fields": {"id": "int"}})["version"], 1)

    def test_resolve_returns_latest(self):
        register({"version": 2, "fields": {}})
        self.assertEqual(resolve()["version"], 2)

    def test_no_history(self):
        register({"version": 1, "fields": {}})
        self.assertNotIn("history", resolve())
