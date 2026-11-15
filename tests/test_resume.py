import json
import os
import tempfile
import threading
import unittest

from taskresume import CheckpointStore, configure, reset, resume, save


class ResumeTest(unittest.TestCase):
    def test_save_is_not_durable(self):
        self.assertFalse(save("t1", {"page": 3})["durable"])

    def test_save_has_no_checksum(self):
        self.assertIsNone(save("t2", {})["checksum"])

    def test_resume_starts_from_zero(self):
        save("t3", {"page": 9})
        self.assertEqual(resume("t3")["start"], 0)


class DurableCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="taskresume-test-")
        configure(self.tmp, keep_versions=3)

    def tearDown(self):
        reset()

    def _task_dir(self, task):
        from urllib.parse import quote

        return os.path.join(self.tmp, quote(str(task), safe=""))

    def _version_files(self, task):
        d = self._task_dir(task)
        if not os.path.isdir(d):
            return []
        return sorted(
            os.path.join(d, n)
            for n in os.listdir(d)
            if n.startswith("checkpoint-") and n.endswith(".json")
        )

    # 位点一致：保存后恢复，位点与跳过页对得上 ---------------------------- #
    def test_position_is_consistent_after_save_resume(self):
        ack = save("job", {"page": 5})
        self.assertTrue(ack["durable"])
        self.assertIsNotNone(ack["checksum"])

        out = resume("job")
        self.assertTrue(out["recovered"])
        self.assertEqual(out["position"], 5)
        self.assertEqual(out["start"], 6)
        self.assertEqual(out["skipped"], [1, 2, 3, 4, 5])
        self.assertFalse(out["rolled_back"])
        self.assertEqual(out["checksum"], ack["checksum"])

    def test_stored_pages_drive_skip_list(self):
        save("job", {"stored_pages": [1, 2, 4]})
        out = resume("job")
        self.assertEqual(out["position"], 4)
        self.assertEqual(out["start"], 5)
        self.assertEqual(out["skipped"], [1, 2, 4])

    def test_explicit_position_overrides_page(self):
        save("job", {"position": 3, "stored_pages": [1, 2], "cursor": "x"})
        out = resume("job")
        self.assertEqual(out["position"], 3)
        self.assertEqual(out["start"], 4)
        self.assertEqual(out["skipped"], [1, 2])

    def test_checkpoint_survives_new_process_like_store(self):
        ack = save("job", {"page": 7})
        # 新存储实例指向同一目录，等价于进程重启
        other = CheckpointStore(self.tmp)
        out = other.resume("job")
        self.assertEqual(out["position"], 7)
        self.assertEqual(out["checksum"], ack["checksum"])

    # 损坏回退 ------------------------------------------------------------ #
    def test_corrupted_latest_falls_back_to_previous_valid(self):
        for page in (1, 2, 3):
            save("job", {"page": page})
        newest = self._version_files("job")[-1]
        with open(newest, "wb") as f:
            f.truncate(7)  # 写一半截断

        out = resume("job")
        self.assertTrue(out["rolled_back"])
        self.assertTrue(out["recovered"])
        self.assertEqual(out["position"], 2)
        self.assertEqual(out["start"], 3)

    def test_checksum_mismatch_falls_back(self):
        for page in (1, 2):
            save("job", {"page": page})
        newest = self._version_files("job")[-1]
        with open(newest, encoding="utf-8") as f:
            record = json.load(f)
        record["position"] = 99  # 篡改内容但保留旧校验和
        with open(newest, "w", encoding="utf-8") as f:
            json.dump(record, f)

        out = resume("job")
        self.assertTrue(out["rolled_back"])
        self.assertEqual(out["position"], 1)

    def test_all_versions_corrupted_falls_back_to_zero(self):
        for page in (1, 2):
            save("job", {"page": page})
        for path in self._version_files("job"):
            with open(path, "wb") as f:
                f.write(b"{not-json")

        out = resume("job")
        self.assertTrue(out["rolled_back"])
        self.assertFalse(out["recovered"])
        self.assertEqual(out["position"], 0)
        self.assertEqual(out["start"], 0)
        self.assertEqual(out["skipped"], [])

    def test_half_written_tmp_is_ignored_and_cleaned_on_next_save(self):
        save("job", {"page": 4})
        d = self._task_dir("job")
        tmp = os.path.join(d, "checkpoint-0000000099-deadbeef.tmp")
        with open(tmp, "wb") as f:
            f.write(b'{"half": true')

        # 恢复不受写一半的临时文件影响
        out = resume("job")
        self.assertEqual(out["position"], 4)
        self.assertTrue(os.path.exists(tmp))

        # 下一次 save 清理遗留临时文件，且只发布合法版本
        save("job", {"page": 5})
        self.assertFalse(os.path.exists(tmp))
        for path in self._version_files("job"):
            with open(path, encoding="utf-8") as f:
                json.load(f)

    # 任务隔离 ------------------------------------------------------------ #
    def test_tasks_are_isolated_in_separate_directories(self):
        save("task-a", {"page": 5})
        save("task-b", {"page": 8})

        entries = sorted(os.listdir(self.tmp))
        self.assertEqual(len(entries), 2)
        self.assertEqual(resume("task-a")["position"], 5)
        self.assertEqual(resume("task-b")["position"], 8)

    def test_concurrent_tasks_do_not_overwrite_each_other(self):
        barrier = threading.Barrier(2)

        def worker(task):
            barrier.wait()
            for page in range(1, 21):
                save(task, {"page": page, "stored_pages": list(range(1, page + 1))})

        t1 = threading.Thread(target=worker, args=("task-a",))
        t2 = threading.Thread(target=worker, args=("task/b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        a = resume("task-a")
        b = resume("task/b")
        self.assertEqual(a["position"], 20)
        self.assertEqual(b["position"], 20)
        self.assertEqual(a["skipped"], list(range(1, 21)))
        self.assertEqual(b["skipped"], list(range(1, 21)))
        # 目录互不相交
        dirs = {os.path.dirname(p) for p in self._version_files("task-a")}
        dirs |= {os.path.dirname(p) for p in self._version_files("task/b")}
        self.assertEqual(len(dirs), 2)

    # 恢复语义 ------------------------------------------------------------ #
    def test_resume_is_idempotent_same_result_every_time(self):
        save("job", {"page": 6})
        first = resume("job")
        second = resume("job")
        self.assertEqual(first, second)
        third = resume("job")
        self.assertEqual(third, first)

    def test_resume_is_read_only_and_interruptible(self):
        save("job", {"page": 3})
        before = sorted(os.listdir(self._task_dir("job")))
        resume("job")  # 中途被杀掉也只是没读完，磁盘不变
        resume("job")
        after = sorted(os.listdir(self._task_dir("job")))
        self.assertEqual(before, after)
        self.assertFalse(any(n.endswith(".tmp") for n in after))

    def test_unknown_task_resumes_from_zero(self):
        out = resume("never-seen")
        self.assertFalse(out["recovered"])
        self.assertEqual(out["start"], 0)
        self.assertEqual(out["position"], 0)
        self.assertEqual(out["skipped"], [])
        self.assertFalse(out["rolled_back"])

    # 版本保留与原子写 ---------------------------------------------------- #
    def test_only_recent_versions_are_kept(self):
        for page in range(1, 6):
            save("job", {"page": page})
        files = self._version_files("job")
        self.assertEqual(len(files), 3)
        self.assertEqual(resume("job")["position"], 5)

    def test_published_files_are_complete_json(self):
        save("job", {"page": 2})
        for path in self._version_files("job"):
            with open(path, encoding="utf-8") as f:
                record = json.load(f)
            self.assertEqual(record["position"], 2)
            self.assertIsInstance(record["checksum"], str)
        leftovers = [
            n
            for n in os.listdir(self._task_dir("job"))
            if n.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])

    def test_negative_position_is_rejected(self):
        with self.assertRaises(ValueError):
            save("job", {"position": -1})


if __name__ == "__main__":
    unittest.main()
