import unittest

from partitionmap import migrate, route


class MapTest(unittest.TestCase):
    def test_route_path(self):
        self.assertEqual(route("orders", "dt=1"), "orders/dt=1")

    def test_migrate_zero(self):
        self.assertEqual(migrate({"from": "a", "to": "b"})["moved"], 0)

    def test_route_keeps_partition(self):
        self.assertIn("dt=1", route("orders", "dt=1"))
