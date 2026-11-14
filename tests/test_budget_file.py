"""文件后端测试：站点额度、超额切换、多进程扣减、日/月对账。

旧的内存模式断言在 test_budget.py 中保持不变；这里每个用例都在临时
目录里启用文件后端，tearDown 恢复内存模式，互不干扰。
"""

import multiprocessing
import os
import shutil
import tempfile
import unittest
from unittest import mock

from proxybudget import budget
from proxybudget import (
    consume,
    remaining,
    configure,
    reset,
    daily_report,
    monthly_report,
)


def _worker(state_dir, site, proxy, amount, times, config_kwargs=None,
            queue=None):
    """子进程入口：各自打开同一份落盘状态做扣减。

    完整配置由参数带入，不依赖父进程内存（spawn 模式下也成立）。
    超额属预期行为时，把 RuntimeError 次数通过 queue 回传而不是让进程崩溃。
    """
    kwargs = {"state_dir": state_dir}
    if config_kwargs:
        kwargs.update(config_kwargs)
    budget.configure(**kwargs)
    failures = 0
    for _ in range(times):
        try:
            budget.consume(site, proxy, amount)
        except RuntimeError:
            failures += 1
    if queue is not None:
        queue.put(failures)


class FileBudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="proxybudget-test-")
        self.config_kwargs = dict(
            default_limit=1000,
            site_limits={"shop": 100, "news": 200},
            proxies={"shop": ["p1", "p2"], "news": ["p3", "p4"]},
        )
        configure(state_dir=self.tmp, **self.config_kwargs)

    def tearDown(self):
        configure()
        budget._spent = 0
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 站点独立额度 ----------------------------------------------------

    def test_site_quotas_are_independent(self):
        result = consume("shop", "p1", 100)
        self.assertEqual(result["spent"], 100)
        self.assertEqual(remaining("shop"), 0)
        # shop 用尽不影响 news 的独立额度。
        self.assertEqual(remaining("news"), 200)
        news_result = consume("news", "p3", 50)
        self.assertEqual(news_result["spent"], 50)
        self.assertEqual(remaining("news"), 150)

    def test_unknown_site_uses_default_limit(self):
        self.assertEqual(remaining("blog"), 1000)
        result = consume("blog", "p9", 10)
        self.assertEqual(result["remaining"], 990)

    def test_state_persists_between_openings(self):
        consume("shop", "p1", 40)
        # 模拟另一个进程重新接入同一目录。
        configure(state_dir=self.tmp, default_limit=1000,
                 site_limits={"shop": 100, "news": 200},
                 proxies={"shop": ["p1", "p2"], "news": ["p3", "p4"]})
        self.assertEqual(remaining("shop"), 60)

    # -- 超额切换 --------------------------------------------------------

    def test_over_quota_switches_proxy_and_returns_wait_time(self):
        consume("shop", "p1", 90)
        result = consume("shop", "p1", 20)  # 90+20 > 100
        self.assertTrue(result["switched"])
        self.assertEqual(result["proxy"], "p2")
        self.assertEqual(result["retry_after_for"], "p1")
        self.assertGreaterEqual(result["retry_after"], 1)
        self.assertLessEqual(result["retry_after"], 24 * 3600)
        # 流量记在新出口上，任务没有中断。
        self.assertEqual(result["spent"], 20)
        self.assertEqual(remaining("shop"), 80)

        report = daily_report()
        self.assertEqual(len(report["switches"]), 1)
        event = report["switches"][0]
        self.assertEqual(event["site"], "shop")
        self.assertEqual(event["from_proxy"], "p1")
        self.assertEqual(event["to_proxy"], "p2")
        self.assertEqual(event["reason"], "quota_exceeded")
        self.assertEqual(event["retry_after"], result["retry_after"])

    def test_retry_after_is_seconds_until_midnight(self):
        import datetime as dt
        fake_now = dt.datetime(2026, 11, 15, 23, 59, 0)
        with mock.patch.object(budget, "_now", return_value=fake_now):
            consume("shop", "p1", 90)
            result = consume("shop", "p1", 20)
        self.assertEqual(result["retry_after"], 60)

    def test_all_proxies_exhausted_raises_with_wait_time(self):
        consume("shop", "p1", 100)
        switched = consume("shop", "p1", 10)  # 切到 p2
        self.assertEqual(switched["proxy"], "p2")
        consume("shop", "p2", 90)              # p2 也用尽
        with self.assertRaises(RuntimeError) as ctx:
            consume("shop", "p2", 1)
        self.assertEqual(ctx.exception.site, "shop")
        self.assertGreaterEqual(ctx.exception.retry_after, 1)

    # -- 多进程原子扣减 --------------------------------------------------

    def test_multiprocess_deduction_matches_total(self):
        # blog 走默认额度 1000，8 进程 * 10 次 * 10 = 800，全部应成功，
        # 落盘合计与请求总量逐一对应，证明原子扣减无丢失、无重复。
        nproc, times, amount = 8, 10, 10
        queue = multiprocessing.Queue()
        procs = [multiprocessing.Process(
            target=_worker,
            args=(self.tmp, "blog", "p1", amount, times,
                  self.config_kwargs, queue))
            for _ in range(nproc)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(sum(queue.get() for _ in procs), 0)

        expected = nproc * times * amount
        report = daily_report()
        self.assertEqual(report["sites"]["blog"]["spent"], expected)
        self.assertEqual(report["sites"]["blog"]["remaining"],
                         1000 - expected)
        self.assertEqual(remaining("blog"), 1000 - expected)

    def test_multiprocess_no_overspend_beyond_limit(self):
        # news 额度 200，两个出口各 200，合计可承接 400。
        # 10 进程 * 10 次 * 10 = 1000 的请求压上去：成功的恰好 400，
        # 其余 600 必须以 RuntimeError 被拒绝，落盘合计绝不能超 400。
        nproc, times, amount = 10, 10, 10
        queue = multiprocessing.Queue()
        procs = [multiprocessing.Process(
            target=_worker,
            args=(self.tmp, "news", "p3", amount, times,
                  self.config_kwargs, queue))
            for _ in range(nproc)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
            self.assertEqual(p.exitcode, 0)
        failures = sum(queue.get() for _ in procs)

        report = daily_report()
        self.assertEqual(report["sites"]["news"]["spent"], 400)
        self.assertEqual(failures, 60)
        # 两个出口都已打满，再来一笔仍被拒绝。
        with self.assertRaises(RuntimeError):
            consume("news", "p3", 1)
        state = budget._load_state_locked()
        self.assertEqual(sum(state["spent"]["news"].values()), 400)

    # -- 日/月对账 -------------------------------------------------------

    def test_daily_and_monthly_reports_across_days(self):
        import datetime as dt
        day1 = dt.datetime(2026, 11, 10, 9, 0, 0)
        day2 = dt.datetime(2026, 11, 11, 10, 30, 0)

        with mock.patch.object(budget, "_now", return_value=day1):
            consume("shop", "p1", 30)
            consume("news", "p3", 20)
        with mock.patch.object(budget, "_now", return_value=day2):
            consume("shop", "p1", 40)
            # 跨天后 p1 额度恢复，剩余按新一天计算。
            self.assertEqual(remaining("shop"), 60)

        day1_report = daily_report("2026-11-10")
        self.assertEqual(day1_report["sites"]["shop"]["spent"], 30)
        self.assertEqual(day1_report["sites"]["shop"]["remaining"], 70)
        self.assertEqual(day1_report["sites"]["news"]["spent"], 20)
        self.assertEqual(day1_report["sites"]["shop"]["by_proxy"],
                         {"p1": 30})

        day2_report = daily_report("2026-11-11")
        self.assertEqual(day2_report["sites"]["shop"]["spent"], 40)

        month = monthly_report("2026-11")
        self.assertEqual(month["sites"]["shop"]["spent"], 70)
        self.assertEqual(month["sites"]["shop"]["by_proxy"], {"p1": 70})
        self.assertEqual(month["sites"]["news"]["spent"], 20)
        # 月内剩余按当月总消耗与站点额度计算。
        self.assertEqual(month["sites"]["shop"]["remaining"], 30)
        self.assertEqual(month["sites"]["news"]["remaining"], 180)

        # 其他月份不受影响。
        self.assertEqual(monthly_report("2026-10")["sites"], {})

    def test_remaining_resets_after_midnight_without_consume(self):
        import datetime as dt
        day1 = budget._now().replace(hour=23, minute=0, second=0,
                                     microsecond=0)
        with mock.patch.object(budget, "_now", return_value=day1):
            consume("shop", "p1", 70)
            day1_str = budget._today()
        next_day = day1 + dt.timedelta(days=1)
        with mock.patch.object(budget, "_now", return_value=next_day):
            # 跨天后即使没有新的 consume，remaining 也必须按新一天满额计算。
            self.assertEqual(remaining("shop"), 100)
        # 昨天的用量仍留在账本里可对账。
        self.assertEqual(
            daily_report(day1_str)["sites"]["shop"]["spent"], 70)

    def test_save_reports_writes_two_files(self):
        consume("shop", "p1", 10)
        day_path, month_path = budget.save_reports()
        self.assertTrue(os.path.exists(day_path))
        self.assertTrue(os.path.exists(month_path))
        self.assertIn("report_daily_", day_path)
        self.assertIn("report_monthly_", month_path)

    def test_reset_clears_state(self):
        consume("shop", "p1", 10)
        reset()
        self.assertEqual(remaining("shop"), 100)
        self.assertEqual(daily_report()["sites"], {})


if __name__ == "__main__":
    unittest.main()
