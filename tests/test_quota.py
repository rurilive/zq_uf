import unittest

from retryquota import release, request


class QuotaTest(unittest.TestCase):
    def test_request_always_granted(self):
        for _ in range(10):
            self.assertTrue(request("task-a", "shop")["granted"])

    def test_round_counts_per_task(self):
        request("task-b", "shop")
        self.assertEqual(request("task-b", "shop")["round"], 2)

    def test_release_reduces_round(self):
        request("task-c", "shop")
        self.assertEqual(release("task-c")["round"], 0)
