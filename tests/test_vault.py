import json
import os
import shutil
import tempfile
import threading
import unittest

import sessionvault
from sessionvault import load, save


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        os.chmod(self.dir, 0o700)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        self.assertEqual(load("a", root=self.dir)["cookie"], "sid=1")

    def test_ciphertext_on_disk(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        raw = open(os.path.join(self.dir, "a.json"), encoding="utf-8").read()
        self.assertNotIn("sid=1", raw)

    def test_envelope_has_key_version(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        envelope = json.load(open(os.path.join(self.dir, "a.json"), encoding="utf-8"))
        self.assertIn("key_version", envelope)
        self.assertIn("checksum", envelope)
        self.assertNotIn("key_version", load("a", root=self.dir))

    def test_rotation_ok(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        save("b", {"cookie": "sid=2"}, root=self.dir)
        result = sessionvault.rotate(self.dir)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["from_version"], 1)
        self.assertEqual(result["to_version"], 2)
        self.assertEqual(sorted(result["rotated"]), ["a", "b"])
        self.assertEqual(sessionvault.current_key_version(self.dir), 2)
        self.assertEqual(load("a", root=self.dir)["cookie"], "sid=1")
        self.assertEqual(load("b", root=self.dir)["cookie"], "sid=2")

    def test_rotation_rollback_on_failure(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        os.chmod(self.dir, 0o500)
        try:
            result = sessionvault.rotate(self.dir)
        finally:
            os.chmod(self.dir, 0o700)
        self.assertEqual(result["status"], "rolled_back")
        self.assertIsNotNone(result["error"])
        self.assertEqual(sessionvault.current_key_version(self.dir), 1)
        self.assertEqual(load("a", root=self.dir)["cookie"], "sid=1")

    def test_corruption_quarantined(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        save("b", {"cookie": "sid=2"}, root=self.dir)
        path = os.path.join(self.dir, "a.json")
        envelope = json.load(open(path, encoding="utf-8"))
        payload = envelope["payload"]
        envelope["payload"] = ("0" if payload[0] != "0" else "1") + payload[1:]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle)
        with self.assertRaises(sessionvault.CorruptRecordError) as ctx:
            load("a", root=self.dir)
        self.assertIn("checksum", ctx.exception.reason)
        self.assertTrue(os.path.exists(ctx.exception.quarantine_path))
        self.assertFalse(os.path.exists(path))
        self.assertEqual(load("b", root=self.dir)["cookie"], "sid=2")
        records = sessionvault.quarantined(self.dir)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["account"], "a")
        self.assertIn("checksum", records[0]["reason"])

    def test_broken_json_quarantined(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        path = os.path.join(self.dir, "a.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(sessionvault.CorruptRecordError):
            load("a", root=self.dir)
        self.assertEqual(len(sessionvault.quarantined(self.dir)), 1)

    def test_concurrent_writes(self):
        errors = []

        def worker(n):
            try:
                for i in range(20):
                    save("acct-%d" % (n % 4), {"cookie": "sid=%d-%d" % (n, i)}, root=self.dir)
                    load("acct-%d" % (n % 4), root=self.dir)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        for n in range(4):
            cookie = load("acct-%d" % n, root=self.dir)["cookie"]
            self.assertRegex(cookie, r"^sid=\d+-\d+$")


if __name__ == "__main__":
    unittest.main()
