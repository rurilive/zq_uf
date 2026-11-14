import json
import os
import tempfile
import threading
import unittest

from filequeue import FileQueue, ack, advance, configure, get, put, reset, stats


class QueueTest(unittest.TestCase):
    def setUp(self):
        reset()

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


class VisibilityQueueTest(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="filequeue-test-")
        configure(self.data_dir, visibility_timeout=30.0, max_deliveries=3)

    def tearDown(self):
        reset()

    def test_concurrent_get_is_exclusive(self):
        total = 50
        for index in range(total):
            put({"id": index})
        consumers = ["c%d" % i for i in range(8)]
        seen = []
        seen_lock = threading.Lock()

        def consume():
            while True:
                msg = get(threading.current_thread().name)
                if msg is None:
                    return
                with seen_lock:
                    seen.append(msg["msg_id"])
                self.assertTrue(ack(msg["msg_id"]))

        threads = [threading.Thread(target=consume, name=name) for name in consumers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(seen), list(range(1, total + 1)))
        self.assertIsNone(get("another"))
        self.assertEqual(stats(), {"visible": 0, "invisible": 0,
                                   "dead": 0, "redeliveries": 0})

    def test_lease_invisible_until_timeout(self):
        msg_id = put({"id": 1})
        first = get("c1")
        self.assertEqual(first["msg_id"], msg_id)
        self.assertEqual(first["deliveries"], 1)
        self.assertIsNone(get("c2"))
        self.assertEqual(stats()["invisible"], 1)

        advance(29.9)
        self.assertIsNone(get("c2"))
        advance(0.2)
        redelivered = get("c2")
        self.assertEqual(redelivered["msg_id"], msg_id)
        self.assertEqual(redelivered["deliveries"], 2)
        self.assertEqual(stats()["redeliveries"], 1)

    def test_dead_letter_after_max_deliveries(self):
        msg_id = put({"id": 1})
        for expected in range(1, 4):
            msg = get("c%d" % expected)
            self.assertEqual(msg["msg_id"], msg_id)
            self.assertEqual(msg["deliveries"], expected)
            advance(30.1)

        self.assertIsNone(get("c4"))
        self.assertEqual(stats(), {"visible": 0, "invisible": 0,
                                   "dead": 1, "redeliveries": 2})

        record_files = [name for name in os.listdir(self.data_dir)
                        if name.endswith(".msg")]
        with open(os.path.join(self.data_dir, record_files[0]),
                  encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["state"], "dead")
        self.assertEqual(record["dead_reason"], "max_deliveries_exceeded")

    def test_ack_is_idempotent(self):
        msg_id = put({"id": 1})
        msg = get("c1")
        self.assertTrue(ack(msg["msg_id"]))
        self.assertFalse(ack(msg_id))
        self.assertFalse(ack(msg_id + 999))
        self.assertIsNone(get("c2"))
        self.assertEqual(stats()["visible"], 0)

    def test_ack_removes_current_lease_without_receipt(self):
        msg_id = put({"id": 1})
        get("c1")
        advance(30.1)
        redelivered = get("c2")
        self.assertEqual(redelivered["deliveries"], 2)
        # ack 只按 msg_id 作用于当前租约：崩溃的 c1 与接手的 c2 确认效果相同，
        # 确认后消息被删除，重复确认变成幂等空操作。
        self.assertTrue(ack(msg_id))
        self.assertFalse(ack(msg_id))
        self.assertIsNone(get("c3"))
        self.assertEqual(stats()["invisible"], 0)

    def test_persistence_survives_worker_restart(self):
        msg_id = put({"id": 7})
        get("crashing-worker")
        self.assertEqual(stats()["invisible"], 1)

        restarted = FileQueue(self.data_dir, visibility_timeout=30.0,
                              max_deliveries=3)
        restarted.advance(30.1)
        msg = restarted.get("replacement")
        self.assertEqual(msg["msg_id"], msg_id)
        self.assertEqual(msg["id"], 7)
        self.assertEqual(msg["deliveries"], 2)
        self.assertTrue(restarted.ack(msg_id))
        self.assertEqual(restarted.stats()["visible"], 0)
        restarted.close()

    def test_checksum_mismatch_goes_dead(self):
        put({"id": 1})
        record_files = [name for name in os.listdir(self.data_dir)
                        if name.endswith(".msg")]
        path = os.path.join(self.data_dir, record_files[0])
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        record["payload"]["id"] = 999
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle)

        self.assertIsNone(get("c1"))
        self.assertEqual(stats()["dead"], 1)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["dead_reason"],
                             "checksum_mismatch")
