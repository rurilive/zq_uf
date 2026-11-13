import unittest

from dagcrawl import run


class FlowTest(unittest.TestCase):
    def test_steps_run_in_order(self):
        calls = []

        def make(name):
            def step(ctx):
                calls.append(name)
                ctx[name] = True
                return ctx

            return step

        ctx = run({"steps": [make("list"), make("detail")]}, {})
        self.assertEqual(calls, ["list", "detail"])
        self.assertTrue(ctx["detail"])

    def test_failure_stops_rest(self):
        calls = []

        def boom(ctx):
            raise ValueError("detail failed")

        def later(ctx):
            calls.append("later")
            return ctx

        with self.assertRaises(ValueError):
            run({"steps": [boom, later]}, {})
        self.assertEqual(calls, [])

    def test_context_is_mutated(self):
        ctx = {}
        run({"steps": [lambda c: c.update({"x": 1}) or c]}, ctx)
        self.assertEqual(ctx, {"x": 1})
