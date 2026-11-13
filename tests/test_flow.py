import unittest

from flowstep import run


class FlowstepTest(unittest.TestCase):
    def test_steps_share_context(self):
        def first(ctx):
            return {"a": 1}

        def second(ctx):
            return {"b": ctx["a"] + 1}

        self.assertEqual(run([first, second], {}), {"a": 1, "b": 2})

    def test_empty_flow(self):
        self.assertEqual(run([], {"x": 1}), {"x": 1})

    def test_input_context_not_mutated(self):
        context = {"x": 1}
        run([lambda ctx: {"y": 2}], context)
        self.assertEqual(context, {"x": 1})
