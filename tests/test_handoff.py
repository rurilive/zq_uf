import subprocess
import sys
import time
import unittest
from pathlib import Path

from captchahandoff import (
    CaptchaRequired,
    CaptchaTimeout,
    challenge,
    reset,
    submit,
    ticket_status,
    tickets,
    wait,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class HandoffTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_challenge_raises(self):
        with self.assertRaises(CaptchaRequired) as ctx:
            challenge("acc-1")
        self.assertEqual(ctx.exception.account, "acc-1")

    def test_submit_without_ticket_raises(self):
        with self.assertRaises(CaptchaRequired):
            submit("acc-1", "1234")

    def test_no_ticket_queue(self):
        with self.assertRaises(CaptchaRequired):
            challenge("acc-2")
        with self.assertRaises(CaptchaRequired):
            submit("acc-2", "0000")

    def test_ticket_created_on_challenge(self):
        with self.assertRaises(CaptchaRequired) as ctx:
            challenge("acc-img", kind="image")
        ticket_id = ctx.exception.ticket_id
        self.assertIsNotNone(ticket_id)
        snap = ticket_status(ticket_id)
        self.assertEqual(snap["status"], "pending")
        self.assertEqual(snap["account"], "acc-img")
        self.assertEqual(snap["kind"], "image")

    def test_account_mutex_no_duplicate_ticket(self):
        with self.assertRaises(CaptchaRequired) as first:
            challenge("acc-mu")
        with self.assertRaises(CaptchaRequired) as second:
            challenge("acc-mu")
        self.assertEqual(first.exception.ticket_id, second.exception.ticket_id)
        mine = [t for t in tickets() if t["account"] == "acc-mu"]
        self.assertEqual(len(mine), 1)

    def test_submit_writes_back_and_releases_account(self):
        with self.assertRaises(CaptchaRequired) as ctx:
            challenge("acc-sms", kind="sms")
        result = submit("acc-sms", "123456")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["code"], "123456")
        self.assertEqual(ticket_status(ctx.exception.ticket_id)["status"], "resolved")
        with self.assertRaises(CaptchaRequired) as ctx2:
            challenge("acc-sms", kind="sms")
        self.assertNotEqual(ctx.exception.ticket_id, ctx2.exception.ticket_id)

    def test_ticket_timeout_marks_failed_and_releases(self):
        with self.assertRaises(CaptchaRequired) as ctx:
            challenge("acc-to", ttl=0.05)
        ticket_id = ctx.exception.ticket_id
        time.sleep(0.1)
        with self.assertRaises(CaptchaTimeout) as err:
            wait("acc-to", poll_interval=0.01)
        self.assertEqual(err.exception.ticket_id, ticket_id)
        self.assertEqual(ticket_status(ticket_id)["status"], "expired")
        with self.assertRaises(CaptchaRequired) as ctx2:
            challenge("acc-to")
        self.assertNotEqual(ticket_id, ctx2.exception.ticket_id)

    def test_submit_after_timeout_raises(self):
        with self.assertRaises(CaptchaRequired):
            challenge("acc-late", ttl=0.05)
        time.sleep(0.1)
        with self.assertRaises(CaptchaRequired) as ctx:
            submit("acc-late", "9f2e")
        self.assertEqual(ctx.exception.status, "expired")

    def test_empty_args_raise_value_error(self):
        with self.assertRaises(ValueError):
            challenge("")
        with self.assertRaises(ValueError):
            challenge("acc-x", ttl=0)

    def test_placeholder_code_rejected_without_consuming_ticket(self):
        with self.assertRaises(CaptchaRequired):
            challenge("acc-ph")
        with self.assertRaises(CaptchaRequired):
            submit("acc-ph", "0000")
        with self.assertRaises(CaptchaRequired):
            submit("acc-ph", "")
        result = submit("acc-ph", "9f2e")
        self.assertEqual(result["status"], "resolved")


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        proc = subprocess.run(
            [sys.executable, "demo.py"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("命中验证码 acc-1 image", proc.stdout)
        self.assertIn("账号互斥 acc-1 同工单 True", proc.stdout)
        self.assertIn("回写继续 acc-1 resolved code 8f3k", proc.stdout)
        self.assertIn("命中验证码 acc-2 sms", proc.stdout)
        self.assertIn("超时失败 acc-2", proc.stdout)
        self.assertIn("expired", proc.stdout)
        self.assertIn("释放重试 acc-2 新工单 True", proc.stdout)
