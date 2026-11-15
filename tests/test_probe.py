import json
import os
import shutil
import tempfile
import time
import unittest

import sitequarantine as sq
from sitequarantine import probe, quarantine


class CycleTest(unittest.TestCase):
    """隔离原因、到期探测、解除、退避、样本、覆盖与共享。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = sq.configure()
        sq.configure(state_path=os.path.join(self.tmp, "state.json"),
                     ttl=0.0, probe_interval=0.0, release_after=1,
                     observation_window=0.0, prober=None)
        sq.reset()

    def tearDown(self):
        sq.configure(**self.saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reason_classified_and_counted(self):
        out = quarantine("t-reason", "connection timeout")
        self.assertEqual(out["reason"], "connection timeout")
        self.assertEqual(out["category"], "timeout")
        quarantine("t-reason2", "http 503")
        categories = sq.stats()["categories"]
        self.assertEqual(categories["timeout"], 1)
        self.assertEqual(categories["http5xx"], 1)

    def test_not_expired_skips_probe(self):
        sq.configure(ttl=60.0)
        quarantine("t-hold", "blocked")
        out = probe("t-hold")
        self.assertEqual(out["verdict"], "isolated")
        self.assertEqual(out["samples"], [])

    def test_probe_after_expiry(self):
        quarantine("t-exp", "blocked")  # ttl=0，立即到期
        self.assertEqual(probe("t-exp")["verdict"], "open")

    def test_consecutive_success_releases_then_observation(self):
        sq.configure(release_after=2, observation_window=0.05)
        quarantine("t-rel", "blocked")
        self.assertEqual(probe("t-rel")["verdict"], "isolated")  # 成功 1/2
        out = probe("t-rel")                                     # 成功 2/2
        self.assertEqual(out["verdict"], "released")
        self.assertEqual(out["weight"], 0.5)                     # 观察期降权
        time.sleep(0.06)
        out = probe("t-rel")                                     # 观察期满放量
        self.assertEqual(out["verdict"], "open")
        self.assertEqual(out["weight"], 1.0)

    def test_failed_probe_extends_quarantine(self):
        sq.configure(ttl=0.1, extend_factor=2.0, prober=lambda host: False)
        quarantine("t-fail", "timeout")
        time.sleep(0.11)  # 等到期
        out = probe("t-fail")
        self.assertEqual(out["verdict"], "isolated")
        self.assertGreater(out["until"], time.time() + 0.1)  # 延长超过原 ttl

    def test_probe_backoff_by_quarantine_count(self):
        sq.configure(probe_interval=60.0, release_after=2)
        quarantine("t-backoff", "blocked")
        probe("t-backoff")                  # 到期，第一次探测
        out = probe("t-backoff")            # 退避中，跳过不探测
        self.assertTrue(out["skipped"])
        self.assertEqual(len(out["samples"]), 1)

    def test_samples_capped(self):
        sq.configure(sample_limit=3, prober=lambda host: False)
        quarantine("t-cap", "blocked")
        for _ in range(5):
            probe("t-cap")
        self.assertEqual(len(sq.status("t-cap")["samples"]), 3)

    def test_manual_override_writes_audit(self):
        sq.configure(ttl=60.0)
        quarantine("t-ovr", "blocked")
        sq.override("t-ovr", "release", operator="ops", note="误封")
        out = probe("t-ovr")
        self.assertEqual(out["verdict"], "open")
        self.assertTrue(out["override"])
        records = sq.audit()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "release")
        self.assertEqual(records[0]["operator"], "ops")

    def test_state_shared_via_disk(self):
        sq.configure(ttl=60.0)
        quarantine("t-share", "blocked")
        path = sq.configure()["state_path"]
        with open(path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertIn("t-share", on_disk["hosts"])
        # 模拟另一进程写入的隔离状态，本进程能读到
        on_disk["hosts"]["t-peer"] = {
            "reason": "dns", "category": "dns", "status": "isolated",
            "until": time.time() + 60, "samples": [], "quarantine_count": 1,
            "consecutive_ok": 0, "consecutive_fail": 0, "last_probe": None,
            "observation_until": None, "override": None}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(on_disk, fh)
        self.assertEqual(sq.status("t-peer")["reason"], "dns")
        self.assertEqual(probe("t-share")["verdict"], "isolated")
