import unittest

from timeoutgov import run, set_clock


class TimeoutTest(unittest.TestCase):
    def setUp(self):
        self.now = [0]
        set_clock(lambda: self.now[0])

    def work_after(self, seconds, value="ok"):
        def work():
            self.now[0] += seconds
            return value

        return work

    def test_fixed_deadline(self):
        with self.assertRaises(TimeoutError):
            run({"name": "small", "work": self.work_after(31)})

    def test_same_limit_for_every_task(self):
        with self.assertRaises(TimeoutError):
            run({"name": "big", "work": self.work_after(31)})

    def test_retry_starts_from_scratch(self):
        calls = []

        def work():
            calls.append(1)
            return "done"

        run({"name": "job", "work": work})
        run({"name": "job", "work": work})
        self.assertEqual(len(calls), 2)

    def test_no_checkpoint(self):
        self.now[0] = 5
        result = run({"name": "job", "work": lambda: "ok"})
        self.assertNotIn("checkpoint", result)
