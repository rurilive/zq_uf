import io
import json
import os
import runpy
import unittest
from contextlib import redirect_stdout

from pushdown import (MAX_BATCH_ROWS, PushError, dump_state, load_state, push,
                      received, reset, resume, set_transport)


class PushTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_counts_rows(self):
        self.assertEqual(push([{"id": 1}, {"id": 2}])["pushed"], 2)

    def test_repeat_sends_full_batch(self):
        push([{"id": 1}])
        result = push([{"id": 1}])
        self.assertEqual(result["pushed"], 1)
        self.assertEqual(result["confirmed"], 0)

    def test_resume_without_token(self):
        self.assertEqual(resume("any")["pushed"], 0)
        self.assertIsNone(resume("any")["token"])


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_rows_carry_version_and_batch(self):
        push([{"id": 1}, {"id": 2}])
        push([{"id": 3}])
        rows = received()
        self.assertEqual([r["_version"] for r in rows], [1, 2, 3])
        self.assertEqual([r["_batch"] for r in rows], [1, 1, 2])

    def test_second_batch_pushes_only_new_rows(self):
        push([{"id": 1}])
        result = push([{"id": 2}, {"id": 3}])
        self.assertEqual(result["pushed"], 2)
        self.assertEqual(len(received()), 3)

    def test_confirm_advances_watermark(self):
        result = push([{"id": 1}])
        ack = resume(result["token"])
        self.assertEqual(ack["pushed"], 0)
        self.assertEqual(ack["confirmed"], 1)
        self.assertEqual(dump_state()["watermark"], 1)

    def test_duplicate_confirm_rejected(self):
        result = push([{"id": 1}])
        resume(result["token"])
        again = resume(result["token"])
        self.assertEqual(again["pushed"], 0)
        self.assertIsNone(again["token"])
        self.assertIn("error", again)
        self.assertEqual(dump_state()["watermark"], 1)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.inbox = []
        self.calls = 0

    def flaky(self, fail_at):
        def transport(row):
            self.calls += 1
            if self.calls == fail_at:
                raise ConnectionError("downstream unavailable")
            self.inbox.append(row)
        return transport

    def test_resume_from_checkpoint_without_duplicates(self):
        set_transport(self.flaky(fail_at=2))
        with self.assertRaises(PushError) as ctx:
            push([{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(ctx.exception.pushed, 1)
        self.assertEqual(ctx.exception.token, "1:1")
        result = resume(ctx.exception.token)
        self.assertEqual(result["pushed"], 2)
        self.assertEqual([r["id"] for r in self.inbox], [1, 2, 3])

    def test_stale_token_rejected(self):
        set_transport(self.flaky(fail_at=2))
        with self.assertRaises(PushError) as ctx:
            push([{"id": 1}, {"id": 2}])
        token = ctx.exception.token
        resume(token)
        stale = resume(token)
        self.assertEqual(stale["pushed"], 0)
        self.assertIn("error", stale)

    def test_unknown_token(self):
        result = resume("9:9")
        self.assertEqual(result["pushed"], 0)
        self.assertIsNone(result["token"])
        self.assertIn("error", result)

    def test_missing_token(self):
        self.assertIn("error", resume())


class ValidationTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_empty_batch_raises(self):
        with self.assertRaises(ValueError):
            push([])

    def test_oversize_batch_raises(self):
        with self.assertRaises(ValueError):
            push([{"id": i} for i in range(MAX_BATCH_ROWS + 1)])

    def test_non_dict_row_raises(self):
        with self.assertRaises(TypeError):
            push([{"id": 1}, "oops"])

    def test_load_state_rejects_garbage(self):
        with self.assertRaises(ValueError):
            load_state({"version": 1})


class StateTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_state_roundtrip_restores_position(self):
        push([{"id": 1}])
        snapshot = json.loads(json.dumps(dump_state()))
        load_state(snapshot)
        result = push([{"id": 9}])
        self.assertEqual(result["batch"], 2)
        self.assertEqual(received()[-1]["_version"], 2)

    def test_resume_after_reload(self):
        result = push([{"id": 1}])
        snapshot = json.loads(json.dumps(dump_state()))
        reset()
        load_state(snapshot)
        ack = resume(result["token"])
        self.assertEqual(ack["confirmed"], 1)


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        reset()
        path = os.path.join(os.path.dirname(__file__), "..", "demo.py")
        buf = io.StringIO()
        with redirect_stdout(buf):
            runpy.run_path(path, run_name="__main__")
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[0], "{'pushed': 2, 'confirmed': 0, 'batch': 1, 'token': '1:2'}")
        self.assertEqual(lines[1], "{'pushed': 0, 'token': None, 'batch': 1, 'confirmed': 1}")
        self.assertTrue(lines[2].startswith("interrupted: batch 2 interrupted at offset 1"))
        self.assertEqual(lines[3], "{'pushed': 2, 'token': '2:3', 'batch': 2, 'confirmed': 1}")
        self.assertEqual(lines[4], "{'pushed': 0, 'token': None, 'batch': 2, 'confirmed': 2}")
        self.assertEqual(lines[5], "inbox: [3, 4, 5]")
        reset()
