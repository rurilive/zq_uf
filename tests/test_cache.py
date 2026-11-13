import json
import multiprocessing as mp
import os
import tempfile
import time
import unittest

import robotscache
from robotscache import (
    CacheConfigError,
    RobotsFetchError,
    allowed,
    configure,
    join_refreshes,
    refresh,
    set_fetcher,
)


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fetcher(host):
            self.calls.append(host)
            return {"disallow": ["/private"]}

        set_fetcher(fetcher)

    def test_disallow_blocks(self):
        self.assertFalse(allowed("http://shop/private/1"))
        self.assertTrue(allowed("http://shop/item/1"))

    def test_every_call_fetches(self):
        allowed("http://shop/item/1")
        allowed("http://shop/item/2")
        self.assertEqual(len(self.calls), 2)

    def test_refresh_fetches_again(self):
        refresh("shop")
        self.assertEqual(self.calls, ["shop"])


def _worker_allowed(url, queue):
    robotscache.configure(ttl=60)

    def fetcher(host):
        raise RuntimeError("缓存命中时不应触发拉取: %s" % host)

    set_fetcher(fetcher)
    try:
        queue.put(("ok", allowed(url)))
    except Exception as exc:
        queue.put(("err", repr(exc)))


def _worker_refresh(host):
    robotscache.configure(ttl=60)
    set_fetcher(lambda h: {"disallow": ["/%s" % h]})
    refresh(host)


class SharedCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache_path = os.path.join(self.tmpdir, "robots-cache.json")
        os.environ["ROBOTSCACHE_PATH"] = self.cache_path
        self.calls = []
        self.rules = {"disallow": ["/private"]}
        self.fail_next = False

        def fetcher(host):
            self.calls.append(host)
            if self.fail_next:
                raise ConnectionError("站点 503")
            return self.rules

        set_fetcher(fetcher)
        configure(cache_path=self.cache_path, ttl=0.05, stale_ttl=60)

    def tearDown(self):
        join_refreshes()
        os.environ.pop("ROBOTSCACHE_PATH", None)
        configure(cache_path=None, ttl=60, stale_ttl=60)

    def _cache_data(self):
        with open(self.cache_path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_cache_hit_fetches_once(self):
        self.assertTrue(allowed("http://shop/item/1"))
        self.assertTrue(allowed("http://shop/item/2"))
        self.assertEqual(self.calls, ["shop"])
        entry = self._cache_data()["hosts"]["shop"]
        self.assertEqual(entry["rules"], {"disallow": ["/private"]})
        self.assertIn("fetched_at", entry)
        with self.assertLogs("robotscache", level="INFO") as logs:
            allowed("http://shop/item/3")
        self.assertTrue(any("缓存命中" in line for line in logs.output))

    def test_expiry_triggers_background_refresh(self):
        self.assertFalse(allowed("http://shop/private/1"))
        self.rules = {"disallow": ["/new-block"]}
        time.sleep(0.08)
        with self.assertLogs("robotscache", level="INFO") as logs:
            stale_result = allowed("http://shop/private/1")
        self.assertFalse(stale_result)
        self.assertTrue(
            any("缓存过期" in line for line in logs.output))
        self.assertTrue(join_refreshes())
        self.assertEqual(self.calls, ["shop", "shop"])
        self.assertFalse(allowed("http://shop/new-block/x"))
        self.assertTrue(allowed("http://shop/private/1"))
        self.assertEqual(
            self._cache_data()["hosts"]["shop"]["rules"], self.rules)

    def test_refresh_failure_keeps_stale_and_warns(self):
        self.assertFalse(allowed("http://shop/private/1"))
        time.sleep(0.08)
        self.fail_next = True
        with self.assertLogs("robotscache", level="WARNING") as logs:
            result = allowed("http://shop/private/1")
        self.assertFalse(result)
        self.assertTrue(join_refreshes())
        self.assertTrue(
            any("后台刷新失败" in line and "shop" in line
                for line in logs.output))

    def test_explicit_refresh_failure_raises(self):
        self.fail_next = True
        with self.assertRaises(RobotsFetchError):
            refresh("shop")

    def test_first_fetch_failure_raises(self):
        self.fail_next = True
        with self.assertRaises(RobotsFetchError):
            allowed("http://brand-new-host/item")

    def test_stale_ttl_forces_sync_refresh(self):
        configure(cache_path=self.cache_path, ttl=0.05, stale_ttl=0.05)
        self.assertTrue(allowed("http://shop/item/1"))
        self.rules = {"disallow": ["/new-block"]}
        time.sleep(0.12)
        with self.assertLogs("robotscache", level="WARNING") as logs:
            result = allowed("http://shop/new-block/x")
        self.assertFalse(result)
        self.assertTrue(
            any("强制同步刷新" in line for line in logs.output))
        self.assertEqual(self.calls, ["shop", "shop"])

    def test_non_dict_rules_raise(self):
        self.rules = ["not-a-dict"]
        with self.assertRaises(RobotsFetchError):
            allowed("http://shop/item")

    def test_invalid_config_raises(self):
        with self.assertRaises(CacheConfigError):
            configure(cache_path=self.cache_path, ttl=-1)

    def test_missing_fetcher_raises(self):
        import robotscache as pkg
        pkg.cache._fetcher = None
        try:
            with self.assertRaises(RuntimeError):
                allowed("http://shop/item")
        finally:
            def fetcher(host):
                self.calls.append(host)
                if self.fail_next:
                    raise ConnectionError("站点 503")
                return self.rules

            set_fetcher(fetcher)

    def test_corrupt_cache_is_backed_up_and_rebuilt(self):
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            handle.write("{not-json")
        with self.assertLogs("robotscache", level="ERROR") as logs:
            self.assertTrue(allowed("http://shop/item/1"))
        self.assertTrue(
            any("缓存文件损坏" in line for line in logs.output))
        backups = [
            name for name in os.listdir(self.tmpdir)
            if name.startswith("robots-cache.json.corrupt-")
        ]
        self.assertEqual(len(backups), 1)
        self.assertEqual(
            self._cache_data()["hosts"]["shop"]["rules"], self.rules)

    def test_shared_cache_read_by_other_process(self):
        refresh("shop")
        queue = mp.Queue()
        proc = mp.Process(
            target=_worker_allowed,
            args=("http://shop/private/1", queue))
        proc.start()
        status, payload = queue.get(timeout=5)
        proc.join(5)
        self.assertEqual(status, "ok", payload)
        self.assertFalse(payload)

    def test_shared_cache_atomic_multi_process_update(self):
        hosts = ["host%d" % i for i in range(4)]
        procs = [
            mp.Process(target=_worker_refresh, args=(host,))
            for host in hosts
        ]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(5)
            self.assertEqual(proc.exitcode, 0)
        data = self._cache_data()
        self.assertEqual(set(data["hosts"]), set(hosts))
        for host in hosts:
            entry = data["hosts"][host]
            self.assertEqual(entry["rules"],
                             {"disallow": ["/%s" % host]})
            self.assertIsInstance(entry["fetched_at"], float)
        leftovers = [
            name for name in os.listdir(self.tmpdir)
            if name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])
