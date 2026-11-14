import threading
import unittest

from filequeue import ack, configure, get, put, reset, stats


class QueueTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        configure(visibility_timeout=1.0, max_deliveries=3)

    def test_put_get(self):
        put({"id": 1})
        self.assertEqual(get("c1")["id"], 1)

    def test_same_message_twice(self):
        put({"id": 1})
        self.assertEqual(get("c1")["id"], get("c2")["id"])

    def test_ack_is_noop(self):
        put({"id": 1})
        ack(1)
        self.assertIsNotNone(get("c1"))

    def test_concurrent_get_single_holder(self):
        configure(visibility_timeout=5.0)
        put({"id": 1})
        results = []
        results_lock = threading.Lock()

        def worker(name):
            msg = get(name, timeout=2)
            with results_lock:
                results.append(msg)

        threads = [
            threading.Thread(target=worker, args=("c%d" % i,)) for i in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        got = [msg for msg in results if msg is not None]
        self.assertEqual(len(got), 1)
        self.assertEqual(stats()["invisible"], 1)

    def test_redelivery_after_visibility_timeout(self):
        configure(visibility_timeout=0.1)
        put({"id": 1})
        first = get("c1")
        self.assertEqual(stats()["invisible"], 1)
        second = get("c2", timeout=2)
        self.assertEqual(second["_msg_id"], first["_msg_id"])
        self.assertEqual(stats()["redeliveries"], 1)

    def test_dead_letter_after_max_redeliveries(self):
        configure(visibility_timeout=0.05, max_deliveries=2)
        put({"id": 1})
        for _ in range(3):
            self.assertIsNotNone(get("c-crash", timeout=1))
        self.assertIsNone(get("c-crash", timeout=0.2))
        self.assertEqual(stats()["dead"], 1)
        self.assertEqual(stats()["visible"], 0)

    def test_ack_is_idempotent(self):
        put({"id": 1})
        msg_id = get("c1")["_msg_id"]
        self.assertTrue(ack(msg_id))
        self.assertTrue(ack(msg_id))
        self.assertTrue(ack(999))
        self.assertEqual(stats()["invisible"], 0)
        self.assertIsNone(get("c1", timeout=0.2))
