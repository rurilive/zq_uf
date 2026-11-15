import unittest

from extractcache import compile, invalidate


class CacheTest(unittest.TestCase):
    def test_compile_is_never_cached(self):
        compile(["a"])
        self.assertFalse(compile(["a"])["cached"])

    def test_compile_counter_grows(self):
        first = compile(["b"])["compiled"]
        self.assertEqual(compile(["b"])["compiled"], first + 1)

    def test_invalidate_hits_nothing(self):
        self.assertEqual(invalidate("v1")["invalidated"], 0)
