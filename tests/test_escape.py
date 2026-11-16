import unittest

from containerexit import check, reset


class EscapeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_privileged_is_high(self):
        got = check({"name": "c1", "host": "h1", "privileged": True})
        self.assertEqual(got["level"], "high")

    def test_socket_mount_is_low(self):
        got = check({
            "name": "c2",
            "host": "h1",
            "privileged": False,
            "mounts": ["/var/run/docker.sock"],
            "caps": ["SYS_ADMIN"],
        })
        self.assertEqual(got["level"], "low")

    def test_basis_only_privileged(self):
        got = check({"name": "c3", "host": "h1", "mounts": ["/proc"]})
        self.assertEqual(got["basis"], [{"key": "privileged", "value": False}])

    def test_advice_text(self):
        got = check({"name": "c4", "host": "h2", "privileged": False})
        self.assertEqual(got["advice"], "no action")

    def test_name_required(self):
        with self.assertRaises(ValueError):
            check({"host": "h1"})
