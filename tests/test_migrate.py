import unittest

from schemamigrate import current, downgrade, upgrade


class MigrateTest(unittest.TestCase):
    def setUp(self):
        from schemamigrate import migrate
        migrate._version["value"] = 1

    def test_upgrade_sets_version(self):
        upgrade(2)
        self.assertEqual(current(), 2)

    def test_downgrade_not_supported(self):
        with self.assertRaises(NotImplementedError):
            downgrade(1)

    def test_no_checksum(self):
        self.assertNotIn("checksum", upgrade(3))
