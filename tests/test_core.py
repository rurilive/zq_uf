import unittest

from dagrun import run


class DagrunTest(unittest.TestCase):
    def test_runs_all(self):
        dag = {"a": lambda: 1, "b": lambda: 2}
        self.assertEqual(run(dag), {"a": 1, "b": 2})

    def test_order_follows_dict(self):
        seen = []
        dag = {"a": lambda: seen.append("a"), "b": lambda: seen.append("b")}
        run(dag)
        self.assertEqual(seen, ["a", "b"])

    def test_empty_dag(self):
        self.assertEqual(run({}), {})
