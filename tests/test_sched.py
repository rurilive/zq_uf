import unittest

from quotasched import Queue


class SchedTest(unittest.TestCase):
    def test_first_come_first_run(self):
        q = Queue()
        q.submit({"id": 1, "cpu": 32, "mem": 64})
        q.submit({"id": 2, "cpu": 1, "mem": 1})
        self.assertEqual(q.next()["id"], 1)

    def test_resource_fields_ignored(self):
        q = Queue()
        q.submit({"id": 1, "cpu": 64})
        self.assertEqual(q.next(), {"id": 1, "cpu": 64})

    def test_no_preemption(self):
        q = Queue()
        q.submit({"id": 1, "cpu": 1})
        self.assertNotIn("preempted", q.next())
