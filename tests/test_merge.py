import unittest

from alertmerge import emit, reset, sent


class MergeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_each_alert_sent(self):
        for index in range(5):
            emit({"job": f"j{index}", "reason": "upstream failed"})
        self.assertEqual(len(sent()), 5)

    def test_recovery_sent_separately(self):
        emit({"job": "j1", "state": "firing"})
        emit({"job": "j1", "state": "recovered"})
        self.assertEqual([row["state"] for row in sent()], ["firing", "recovered"])

    def test_no_aggregate_fields(self):
        emit({"job": "j1", "state": "firing"})
        self.assertEqual(set(sent()[0].keys()), {"job", "state"})
