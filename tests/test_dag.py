import unittest

from jobdep import run


class DagTest(unittest.TestCase):
    def test_steps_in_order(self):
        seen = []
        state = run({"steps": [{"name": "a", "fn": lambda s: seen.append("a")},
                               {"name": "b", "fn": lambda s: seen.append("b")}]})
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(state["b"], "done")

    def test_failure_stops(self):
        seen = []
        with self.assertRaises(ValueError):
            run({"steps": [{"name": "a", "fn": lambda s: (_ for _ in ()).throw(ValueError())},
                           {"name": "b", "fn": lambda s: seen.append("b")}]})
        self.assertEqual(seen, [])

    def test_state_records_done(self):
        self.assertEqual(run({"steps": [{"name": "a", "fn": lambda s: None}]})["a"], "done")
