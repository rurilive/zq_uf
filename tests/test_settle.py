import os
import shutil
import tempfile
import unittest

import proxysettle
from proxysettle import charge, settle


class SettleTest(unittest.TestCase):
    def test_charge_accumulates(self):
        charge("p1", 1.5)
        self.assertEqual(charge("p1", 2.5)["balance"], 4.0)

    def test_failed_request_also_charged(self):
        charge("p2", 1.0)
        charge("p2", 1.0)
        self.assertEqual(settle("p2")["balance"], 2.0)

    def test_settle_has_no_diff(self):
        self.assertEqual(settle("p3")["diff"], 0.0)


class OutcomeBillingTest(unittest.TestCase):
    def setUp(self):
        proxysettle.reset()

    def tearDown(self):
        proxysettle.reset()

    def test_success_stored_is_billed(self):
        r = charge("p1", 2.0, success=True, stored=True, batch="b1")
        self.assertEqual(r["outcome"], "billed")
        self.assertEqual(r["refund"], 0.0)
        self.assertEqual(settle("p1")["balance"], 2.0)

    def test_failed_request_is_refunded(self):
        r = charge("p2", 3.0, success=False, batch="b1")
        self.assertEqual(r["outcome"], "failed")
        self.assertEqual(r["refund"], 3.0)
        s = settle("p2")
        self.assertEqual(s["charges"], 3.0)
        self.assertEqual(s["refunds"], 3.0)
        self.assertEqual(s["balance"], 0.0)

    def test_timeout_request_is_refunded(self):
        r = charge("p3", 4.0, timeout=True, batch="b1")
        self.assertEqual(r["outcome"], "timeout")
        self.assertEqual(r["refund"], 4.0)
        self.assertEqual(settle("p3")["balance"], 0.0)

    def test_success_not_stored_is_refunded(self):
        r = charge("p4", 5.0, success=True, stored=False, batch="b1")
        self.assertEqual(r["outcome"], "not_stored")
        self.assertEqual(r["refund"], 5.0)
        self.assertEqual(settle("p4")["balance"], 0.0)


class SwitchProxyTest(unittest.TestCase):
    def setUp(self):
        proxysettle.reset()

    def tearDown(self):
        proxysettle.reset()

    def test_switch_refunds_old_proxy_and_links_batch(self):
        charge("old", 1.0, batch="b1")
        charge("old", 2.0, batch="b1")
        # 已退费的失败流水不应在切换时重复退费
        charge("old", 1.0, success=False, batch="b1")

        rec = proxysettle.switch_proxy("old", "new", batch="b1",
                                       reason="exit-down")

        self.assertEqual(rec["old_proxy"], "old")
        self.assertEqual(rec["new_proxy"], "new")
        self.assertEqual(rec["batch"], "b1")
        self.assertEqual(rec["reason"], "exit-down")
        self.assertEqual(rec["refund_amount"], 3.0)
        self.assertEqual(len(rec["refund_entry_ids"]), 2)
        # 旧出口余额全部退回
        self.assertEqual(proxysettle.balance("old"), 0.0)

        # 新出口独立计费，不受旧出口影响
        charge("new", 1.5, batch="b1")
        self.assertEqual(proxysettle.balance("new"), 1.5)

        s = settle("old")
        self.assertEqual(s["refunds"], 4.0)  # 失败退费 1.0 + 切换退费 3.0
        self.assertEqual(s["switches"][0]["old_proxy"], "old")
        self.assertEqual(s["switches"][0]["batch"], "b1")


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        proxysettle.reset()

    def tearDown(self):
        proxysettle.reset()

    def test_diff_zero_when_rows_match_billable(self):
        charge("p1", 1.0, batch="b1")
        charge("p1", 1.0, batch="b1")
        charge("p1", 1.0, success=False, batch="b1")
        proxysettle.mark_stored("p1", 2)
        s = settle("p1")
        self.assertEqual(s["billable_count"], 2)
        self.assertEqual(s["stored_rows"], 2)
        self.assertEqual(s["diff"], 0.0)
        self.assertTrue(s["reconciled"])

    def test_diff_reports_row_mismatch(self):
        charge("p2", 1.0, batch="b1")
        charge("p2", 1.0, batch="b1")
        proxysettle.mark_stored("p2", 1)
        s = settle("p2")
        self.assertEqual(s["stored_rows"], 1)
        self.assertEqual(s["diff"], 1.0)

    def test_settle_is_idempotent(self):
        charge("p3", 1.0, batch="b1")
        first = settle("p3")
        second = settle("p3")
        self.assertEqual(first["balance"], second["balance"])
        self.assertEqual(first["entries"], second["entries"])


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="proxysettle-test-")
        proxysettle.reset(self.tmp)

    def tearDown(self):
        proxysettle.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ledger_and_settlement_survive_restart(self):
        charge("p1", 2.0, batch="b1")
        charge("p1", 3.0, success=False, batch="b1")
        proxysettle.switch_proxy("p1", "p2", batch="b1")
        # 切换把 p1 唯一一条计费退回，实际无计费入库行
        proxysettle.mark_stored("p1", 0)
        on_disk = settle("p1")

        # 模拟进程重启：用同一数据目录新建账本
        restarted = proxysettle.SettleBook(self.tmp)
        self.assertEqual(restarted.balance("p1"), on_disk["balance"])
        self.assertEqual(len(restarted.switches), 1)
        self.assertEqual(restarted.switches[0]["old_proxy"], "p1")
        self.assertEqual(restarted.switches[0]["batch"], "b1")

        loaded = restarted.load_settlement(on_disk["date"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["proxies"]["p1"]["diff"], 0.0)
        self.assertEqual(loaded["proxies"]["p1"]["stored_rows"], 0)
        self.assertTrue(
            os.path.exists(os.path.join(self.tmp, "settlements",
                                        on_disk["date"] + ".json"))
        )
