import csv
import json
import os
import tempfile
import unittest

from formbatch import SubmitError, export_detail, resume, submit


class BatchTest(unittest.TestCase):
    def test_submit_all_forms(self):
        result = submit([{"name": "a"}, {"name": "b"}])
        self.assertEqual(len(result["results"]), 2)

    def test_submit_marks_every_row_ok(self):
        result = submit([{"name": "a"}])
        self.assertTrue(result["results"][0]["ok"])

    def test_resume_has_no_state(self):
        self.assertFalse(resume("last")["resumed"])


class IdempotencyTest(unittest.TestCase):
    def test_success_not_resent(self):
        calls = []

        def sender(form, seq):
            calls.append(seq)
            return {"ok": True}

        forms = [{"name": "idem1"}, {"name": "idem2"}]
        submit(forms, sender=sender)
        second = submit(forms, sender=sender)
        self.assertEqual(len(calls), 2)
        self.assertEqual(second["summary"]["skipped"], 2)
        self.assertTrue(all(it["ok"] for it in second["results"]))

    def test_explicit_idempotency_key(self):
        calls = []

        def sender(form, seq):
            calls.append(seq)
            return {"ok": True}

        submit([{"name": "x1", "idempotency_key": "k-1"}], sender=sender)
        # 内容不同但键相同，同样跳过
        second = submit([{"name": "x2", "idempotency_key": "k-1"}], sender=sender)
        self.assertEqual(len(calls), 1)
        self.assertEqual(second["results"][0]["status"], "skipped")

    def test_duplicate_key_within_batch(self):
        forms = [{"name": "dup1", "idempotency_key": "k-dup"},
                 {"name": "dup1", "idempotency_key": "k-dup"}]
        result = submit(forms)
        statuses = sorted(it["status"] for it in result["results"])
        self.assertEqual(statuses, ["skipped", "succeeded"])

    def test_key_expires_after_ttl(self):
        calls = []

        def sender(form, seq):
            calls.append(seq)
            return {"ok": True}

        submit([{"name": "ttl1"}], sender=sender, idempotency_ttl=0)
        submit([{"name": "ttl1"}], sender=sender, idempotency_ttl=0)
        self.assertEqual(len(calls), 2)


class RetryTest(unittest.TestCase):
    def test_retry_then_success(self):
        calls = {"n": 0}

        def sender(form, seq):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": False, "category": "server", "message": "HTTP 500"}
            return {"ok": True}

        result = submit([{"name": "retry1"}], sender=sender, backoff_base=0)
        item = result["results"][0]
        self.assertTrue(item["ok"])
        self.assertEqual(item["attempts"], 2)

    def test_retry_cap_and_reason(self):
        def sender(form, seq):
            return {"ok": False, "category": "server", "message": "HTTP 500"}

        result = submit([{"name": "cap1"}], sender=sender,
                        max_retries=2, backoff_base=0)
        item = result["results"][0]
        self.assertFalse(item["ok"])
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["attempts"], 3)  # 首次 + 2 次重试
        self.assertEqual(item["reason"]["category"], "server")
        self.assertEqual(item["reason"]["seq"], item["seq"])

    def test_client_error_not_retried(self):
        calls = []

        def sender(form, seq):
            calls.append(seq)
            return {"ok": False, "category": "client", "message": "HTTP 400"}

        result = submit([{"name": "client1"}], sender=sender, backoff_base=0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["results"][0]["reason"]["category"], "client")

    def test_exception_carries_seq(self):
        def sender(form, seq):
            raise SubmitError("boom", category="network")

        result = submit([{"name": "exc1"}, {"name": "exc2"}], sender=sender,
                        max_retries=0, backoff_base=0)
        self.assertEqual(result["results"][1]["reason"]["seq"], 1)
        self.assertEqual(result["results"][1]["reason"]["category"], "network")

    def test_resume_retries_only_failed(self):
        def sender(form, seq):
            if form["name"] == "res-bad":
                return {"ok": False, "category": "client", "message": "HTTP 400"}
            return {"ok": True}

        forms = [{"name": "res-ok"}, {"name": "res-bad"}]
        first = submit(forms, sender=sender, backoff_base=0)
        self.assertEqual(first["summary"]["failed"], 1)

        seen = []

        def sender2(form, seq):
            seen.append(form["name"])
            return {"ok": True}

        out = resume(first["batch"], sender=sender2)
        self.assertTrue(out["resumed"])
        self.assertEqual(seen, ["res-bad"])
        self.assertEqual(out["summary"]["succeeded"], 2)
        self.assertEqual(out["summary"]["failed"], 0)

    def test_resume_unknown_batch(self):
        out = resume("no-such-batch")
        self.assertFalse(out["resumed"])
        self.assertEqual(out["results"], [])


class RateLimitTest(unittest.TestCase):
    def _clock_pair(self):
        t = [0.0]

        def clock():
            return t[0]

        def sleep(seconds):
            t[0] += seconds

        return clock, sleep

    def test_site_layer_waits(self):
        clock, sleep = self._clock_pair()
        forms = [{"name": f"rl{i}", "site": "s1"} for i in range(3)]
        result = submit(forms, site_rate=2, max_workers=1,
                        clock=clock, sleep=sleep)
        summary = result["summary"]
        self.assertEqual(summary["succeeded"], 3)
        self.assertEqual(summary["rate_limit_waits"], 2)
        self.assertAlmostEqual(summary["rate_limit_wait_seconds"], 1.0)

    def test_global_layer_spans_sites(self):
        clock, sleep = self._clock_pair()
        forms = [{"name": f"rg{i}", "site": f"site{i}"} for i in range(3)]
        result = submit(forms, global_rate=2, max_workers=1,
                        clock=clock, sleep=sleep)
        summary = result["summary"]
        self.assertEqual(summary["rate_limit_waits"], 2)
        self.assertAlmostEqual(summary["rate_limit_wait_seconds"], 1.0)

    def test_rate_limited_response_counts_as_wait(self):
        clock, sleep = self._clock_pair()
        calls = {"n": 0}

        def sender(form, seq):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": False, "category": "rate_limited",
                        "message": "HTTP 429"}
            return {"ok": True}

        result = submit([{"name": "rl429"}], sender=sender,
                        backoff_base=0.25, clock=clock, sleep=sleep)
        self.assertTrue(result["results"][0]["ok"])
        self.assertEqual(result["summary"]["rate_limit_waits"], 1)
        self.assertAlmostEqual(
            result["summary"]["rate_limit_wait_seconds"], 0.25)


class ReconcileTest(unittest.TestCase):
    def test_results_align_with_requests(self):
        forms = [{"name": f"rec{i}"} for i in range(5)]
        result = submit(forms)
        self.assertEqual([it["index"] for it in result["results"]],
                         list(range(5)))
        self.assertEqual([it["seq"] for it in result["results"]],
                         list(range(5)))

    def test_export_detail_rows(self):
        def sender(form, seq):
            if form["name"] == "exp-bad":
                return {"ok": False, "category": "client",
                        "message": "HTTP 400"}
            return {"ok": True}

        result = submit([{"name": "exp-ok"}, {"name": "exp-bad"}],
                        sender=sender, backoff_base=0)
        rows = export_detail(result["batch"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "succeeded")
        self.assertEqual(rows[1]["status"], "failed")
        self.assertEqual(rows[1]["category"], "client")
        self.assertEqual(json.loads(rows[1]["form"]), {"name": "exp-bad"})

    def test_export_detail_to_files(self):
        result = submit([{"name": "exp-file1"}, {"name": "exp-file2"}])
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = os.path.join(tmp, "detail.jsonl")
            csv_path = os.path.join(tmp, "detail.csv")
            export_detail(result["batch"], jsonl)
            export_detail(result["batch"], csv_path)
            with open(jsonl, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual(len(lines), 2)
            with open(csv_path, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["status"], "succeeded")

    def test_export_unknown_batch(self):
        self.assertEqual(export_detail("no-such-batch"), [])


class ConcurrencyTest(unittest.TestCase):
    def test_concurrent_results_complete(self):
        forms = [{"name": f"cc{i}"} for i in range(10)]
        result = submit(forms, max_workers=4)
        self.assertEqual(result["summary"]["succeeded"], 10)
        self.assertEqual(sorted(it["seq"] for it in result["results"]),
                         list(range(10)))


if __name__ == "__main__":
    unittest.main()
