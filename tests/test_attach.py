"""attachcheck 行为测试：全部离线，用 ScriptedTransport 模拟站点。"""

import base64
import hashlib
import os
import tempfile
import unittest

from attachcheck import AttachmentStore, ScriptedTransport, fetch, verify

URL = "https://shop.example/file"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def digest_header(data, algorithm="sha-256"):
    raw = hashlib.new(algorithm.replace("-", ""), data).digest()
    return f"{algorithm}={base64.b64encode(raw).decode()}"


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.site = ScriptedTransport()

    def store(self, **kwargs):
        return AttachmentStore(self.tmp, transport=self.site, **kwargs)

    def target(self, name="file.bin"):
        return os.path.join(self.tmp, name)

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    # ---------------------------------------------------------------- 正常下载

    def test_fetch_writes_complete_file(self):
        content = b"hello attachment" * 100
        self.site.add(URL, content)
        result = self.store().fetch(URL, self.target())

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["bytes"], len(content))
        self.assertEqual(result["path"], self.target())
        self.assertEqual(self.read(self.target()), content)
        self.assertFalse(result["resumed"])
        self.assertFalse(result["reused"])
        self.assertTrue(result["verified"]["ok"])
        self.assertEqual(result["verified"]["checksum"], sha256(content))
        self.assertEqual(result["status"], 200)

    def test_no_part_files_left_after_success(self):
        self.site.add(URL, b"abc" * 500)
        self.store().fetch(URL, self.target())
        leftovers = []
        for base, _, names in os.walk(self.tmp):
            for name in names:
                if name.endswith(".part"):
                    leftovers.append(os.path.join(base, name))
        self.assertEqual(leftovers, [])
        # 目标目录顶层只有正式文件，中间状态都在 .attachcheck 里。
        self.assertEqual(sorted(os.listdir(self.tmp)), [".attachcheck", "file.bin"])

    # ---------------------------------------------------------------- 断线与续传

    def test_disconnect_leaves_no_final_file_and_records_quarantine(self):
        content = b"0123456789" * 20
        self.site.add(URL, content)
        self.site.script(URL, {"cut": 70})
        store = self.store()
        result = store.fetch(URL, self.target())

        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(self.target()), "半个文件不能出现在目标目录")
        self.assertEqual(result["bytes"], 70)
        self.assertEqual(result["quarantine"], result["quarantine"])
        entries = store.list_quarantine()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["url"], URL)
        self.assertEqual(entries[0]["reason"], "disconnect")
        self.assertEqual(entries[0]["bytes"], 70)

    def test_resume_from_saved_offset(self):
        content = bytes(range(256)) * 8
        self.site.add(URL, content, etag='"v1"')
        self.site.script(URL, {"cut": 300})
        store = self.store()

        first = store.fetch(URL, self.target())
        self.assertFalse(first["ok"])
        self.assertEqual(first["bytes"], 300)

        second = store.fetch(URL, self.target())
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["resumed"])
        self.assertEqual(second["bytes"], len(content))
        self.assertEqual(second["received"], len(content) - 300)
        self.assertEqual(self.read(self.target()), content)

        # 第二次请求带 Range 与 If-Range，并从 300 开始。
        resumed_request = self.site.requests[-1]
        self.assertEqual(resumed_request["range"], "bytes=300-")
        self.assertEqual(resumed_request["if_range"], '"v1"')
        self.assertEqual(resumed_request["status"], 206)

        # 续传成功后隔离区清空。
        self.assertEqual(store.list_quarantine(), [])

    def test_resume_rejects_corrupted_prefix(self):
        content = b"abcdefghij" * 30
        self.site.add(URL, content)
        self.site.script(URL, {"cut": 120})
        store = self.store()
        store.fetch(URL, self.target())

        parts = os.path.join(self.tmp, ".attachcheck", "parts")
        part = [n for n in os.listdir(parts) if n.endswith(".part")][0]
        with open(os.path.join(parts, part), "r+b") as handle:
            handle.seek(0)
            handle.write(b"XXXX")

        result = store.fetch(URL, self.target())
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["resumed"], "前缀校验失败必须从头重下")
        self.assertEqual(self.read(self.target()), content)
        self.assertEqual(self.site.requests[-1]["range"], None)

    def test_cleanish_short_body_is_kept_for_resume(self):
        content = b"z" * 200
        self.site.add(URL, content)
        self.site.script(URL, {"short_body": True})
        store = self.store()
        result = store.fetch(URL, self.target())
        self.assertFalse(result["ok"])
        self.assertEqual(result["verified"]["reason"], "incomplete")

        second = store.fetch(URL, self.target())
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["resumed"])
        self.assertEqual(self.read(self.target()), content)

    def test_server_ignoring_range_restarts_cleanly(self):
        content = b"y" * 300
        self.site.add(URL, content)
        self.site.script(URL, {"cut": 100})
        store = self.store()
        self.assertFalse(store.fetch(URL, self.target())["ok"])

        self.site.script(URL, {"ignore_range": True})
        result = store.fetch(URL, self.target())
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["resumed"])
        self.assertEqual(self.read(self.target()), content)

    # -------------------------------------------------------------- 内容变化

    def test_changed_content_restarts_after_if_range_miss(self):
        old = b"old" * 100
        new = b"new" * 120
        self.site.add(URL, old, etag='"v1"')
        self.site.script(URL, {"cut": 50})
        store = self.store()
        store.fetch(URL, self.target())

        # 站点内容已更新，If-Range 失配时服务器回 200 全量。
        self.site.script(URL, {"content": new, "etag": '"v2"'})
        result = store.fetch(URL, self.target(), refresh=True)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["resumed"])
        self.assertEqual(self.read(self.target()), new)

    # -------------------------------------------------------------- 同名与复用

    def test_same_name_different_content_keeps_both(self):
        first = b"version one"
        second = b"version two, different"
        self.site.add("https://shop.example/a", first)
        self.site.add("https://shop.example/b", second)
        store = self.store()

        r1 = store.fetch("https://shop.example/a", self.target("doc.pdf"))
        r2 = store.fetch("https://shop.example/b", self.target("doc.pdf"))

        self.assertTrue(r1["ok"])
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r1["path"], self.target("doc.pdf"))
        self.assertNotEqual(r2["path"], r1["path"])
        self.assertTrue(r2["path"].endswith(".pdf"))
        self.assertEqual(self.read(r1["path"]), first)
        self.assertEqual(self.read(r2["path"]), second)

    def test_repeat_download_reuses_without_request(self):
        content = b"cache me"
        self.site.add(URL, content)
        store = self.store()
        first = store.fetch(URL, self.target())
        requests = len(self.site.requests)
        second = store.fetch(URL, self.target())

        self.assertTrue(second["ok"])
        self.assertTrue(second["reused"])
        self.assertFalse(second["resumed"])
        self.assertEqual(second["bytes"], len(content))
        self.assertEqual(second["received"], 0)
        self.assertEqual(len(self.site.requests), requests, "复用不应再请求站点")

    def test_reuse_detects_locally_tampered_file(self):
        content = b"tamper target"
        self.site.add(URL, content)
        store = self.store()
        store.fetch(URL, self.target())
        with open(self.target(), "wb") as handle:
            handle.write(b"tampered")

        result = store.fetch(URL, self.target())
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["reused"])
        self.assertEqual(self.read(self.target()), content)

    def test_unknown_file_at_target_is_not_overwritten(self):
        # 目标位置有个不是本工具下载的文件：不能悄悄覆盖。
        foreign = self.target()
        with open(foreign, "wb") as handle:
            handle.write(b"user's own file")
        self.site.add(URL, b"downloaded content")

        result = self.store().fetch(URL, foreign)
        self.assertTrue(result["ok"], result)
        self.assertNotEqual(result["path"], foreign)
        self.assertEqual(self.read(foreign), b"user's own file")
        self.assertEqual(self.read(result["path"]), b"downloaded content")

    # ---------------------------------------------------------------- 内容校验

    def test_server_digest_mismatch_goes_to_quarantine(self):
        content = b"good bytes"
        self.site.add(URL, content)
        site = self.site
        original_open = site.open

        def bad_open(url, **kwargs):
            response = original_open(url, **kwargs)
            response.headers["digest"] = digest_header(b"something else")
            return response

        site.open = bad_open
        store = self.store()
        result = store.fetch(URL, self.target())

        self.assertFalse(result["ok"])
        self.assertEqual(result["verified"]["reason"], "checksum_mismatch")
        self.assertFalse(os.path.exists(self.target()))
        self.assertIsNotNone(result["quarantine"])
        entry = store.quarantine_entry(result["quarantine"])
        self.assertEqual(entry["url"], URL)
        payload = os.path.join(self.tmp, ".attachcheck", "quarantine",
                               entry["id"], entry["payload"])
        self.assertEqual(self.read(payload), content)

    def test_matching_server_digest_passes(self):
        content = b"verified content"
        self.site.add(URL, content)
        original_open = self.site.open

        def good_open(url, **kwargs):
            response = original_open(url, **kwargs)
            response.headers["digest"] = digest_header(content)
            return response

        self.site.open = good_open
        result = self.store().fetch(URL, self.target())
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.read(self.target()), content)

    def test_verify_detects_length_and_checksum_damage(self):
        content = b"a" * 40
        self.site.add(URL, content)
        store = self.store()
        store.fetch(URL, self.target())

        self.assertTrue(store.verify(self.target())["ok"])

        with open(self.target(), "r+b") as handle:
            handle.seek(10)
            handle.write(b"ZZ")
        result = store.verify(self.target())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "checksum_mismatch")

        with open(self.target(), "ab") as handle:
            handle.write(b"extra")
        result = store.verify(self.target())
        self.assertEqual(result["reason"], "length_mismatch")

    def test_verify_can_quarantine_damaged_file(self):
        self.site.add(URL, b"payload")
        store = self.store()
        store.fetch(URL, self.target())
        with open(self.target(), "wb") as handle:
            handle.write(b"broken")
        result = store.verify(self.target(), quarantine=True)
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(self.target()))
        self.assertEqual(len(store.list_quarantine()), 1)

    # ---------------------------------------------------------------- 隔离区重试

    def test_http_error_keeps_url_and_status_and_can_retry(self):
        self.site.script(URL, {"status": 503})
        store = self.store()
        result = store.fetch(URL, self.target())

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 503)
        entry = store.quarantine_entry(result["quarantine"])
        self.assertEqual(entry["url"], URL)
        self.assertEqual(entry["status"], 503)
        self.assertEqual(entry["attempts"], 1)

        self.site.add(URL, b"now available")
        retried = store.retry(entry)
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(self.read(self.target()), b"now available")
        self.assertEqual(store.list_quarantine(), [])

    def test_retry_after_disconnect_resumes(self):
        content = b"resume via retry" * 10
        self.site.add(URL, content)
        self.site.script(URL, {"cut": 40})
        store = self.store()
        failed = store.fetch(URL, self.target())
        self.assertFalse(failed["ok"])

        retried = store.retry(failed["quarantine"])
        self.assertTrue(retried["ok"], retried)
        self.assertTrue(retried["resumed"])
        self.assertEqual(self.read(self.target()), content)

    # ------------------------------------------------------------ 单站点观察

    def test_non_canary_site_neither_resumes_nor_quarantines(self):
        content = b"normal attachment"
        other = "https://cdn.example/asset"
        self.site.add(other, content)
        self.site.script(other, {"cut": 5})
        store = self.store(canary_sites=["shop.example"])

        result = store.fetch(other, self.target("asset.bin"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "simple")
        self.assertIsNone(result["quarantine"])
        self.assertEqual(store.list_quarantine(), [])
        # 没留中间文件，下一次从头下载。
        parts = os.path.join(self.tmp, ".attachcheck", "parts")
        self.assertEqual([n for n in os.listdir(parts) if n.endswith(".part")], [])

    def test_canary_site_behaves_normally_alongside(self):
        content = b"observed site"
        self.site.add(URL, content)
        store = self.store(canary_sites=["shop.example"])
        result = store.fetch(URL, self.target())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "canary")
        self.assertEqual(self.read(self.target()), content)

    # ---------------------------------------------------------------- 模块入口

    def test_module_level_fetch_and_verify(self):
        content = b"module api"
        self.site.add(URL, content)
        result = fetch(URL, self.target(), transport=self.site)
        self.assertTrue(result["ok"])
        self.assertEqual(verify(self.target())["checksum"], sha256(content))


if __name__ == "__main__":
    unittest.main()
