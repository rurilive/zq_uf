import os
import tempfile
import unittest

from filerouter import route


class FilerouterTest(unittest.TestCase):
    def test_route_by_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.log")
            open(src, "w").write("x")
            out = route(src, os.path.join(tmp, "out"))
            self.assertTrue(out.endswith(os.path.join("log", "a.log")))
