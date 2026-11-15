import json
import logging
import os
import tempfile
import threading
import unittest
from unittest import mock

from jobrecover import DEFAULT_STAGES, JobRecovery, StageDependencyError
from jobrecover import resume, save
from jobrecover.recover import CheckpointError, _atomic_write


# --------------------------------------------------------------------------
# 历史测试：断言原样保留，模块级内存版函数继续满足旧契约
# --------------------------------------------------------------------------

class RecoverTest(unittest.TestCase):
    def test_save_has_no_checksum(self):
        self.assertIsNone(save("j1", "fetch", {"page": 1})["checksum"])

    def test_resume_restarts(self):
        save("j2", "parse", {})
        self.assertEqual(resume("j2")["stage"], "start")

    def test_resume_restores_nothing(self):
        self.assertFalse(resume("j3")["restored"])


# --------------------------------------------------------------------------
# 阶段级恢复：JobRecovery
# --------------------------------------------------------------------------

class JobRecoveryBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name
        self.r = JobRecovery(self.base)

    def tearDown(self):
        self._tmp.cleanup()

    def job_dir(self, job):
        from urllib.parse import quote

        return os.path.join(self.base, quote(job, safe=""))


class StagePositionTest(JobRecoveryBase):
    def test_checkpoint_records_stage_position_version_checksum(self):
        self.r.save("job-a", "start", {})
        result = self.r.save("job-a", "fetch", {"position": 5, "page": 5})
        self.assertTrue(result["saved"])
        self.assertEqual(result["stage"], "fetch")
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["position"], 5)
        self.assertTrue(result["checksum"].startswith("sha256:"))

        point = self.r.resume("job-a")
        self.assertEqual(point["stage"], "parse")  # fetch 已完成，只跑后续阶段
        self.assertEqual(point["position"], 5)
        self.assertEqual(point["state"]["page"], 5)
        self.assertEqual(point["version"], 2)
        self.assertEqual(point["completed"], ["start", "fetch"])
        self.assertFalse(point["rollback"])
        self.assertTrue(point["restored"])

    def test_version_increments_monotonically_and_stage_states_keep_versions(self):
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {"position": 3})
        self.r.save("job-a", "parse", {"position": 3})
        point = self.r.resume("job-a")
        self.assertEqual(point["version"], 3)
        self.assertEqual(point["stages"]["fetch"]["version"], 2)
        self.assertEqual(point["stages"]["parse"]["version"], 3)
        self.assertEqual(point["stages"]["parse"]["position"], 3)
        self.assertEqual(point["stage"], "store")  # start/fetch/parse 均已完成
        self.assertEqual(point["completed"], ["start", "fetch", "parse"])

    def test_repeated_save_within_stage_advances_position_only(self):
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {"position": 1, "pages": [1]}, complete=False)
        self.r.save("job-a", "fetch", {"position": 2, "pages": [1, 2]}, complete=False)
        point = self.r.resume("job-a")
        self.assertEqual(point["stage"], "fetch")  # 位点点不标记完成
        self.assertEqual(point["completed"], ["start"])
        self.assertEqual(point["position"], 2)
        self.assertEqual(point["stored_pages"], [1, 2])
        self.assertEqual(point["version"], 3)
        self.assertFalse(point["stage_complete"])

    def test_position_save_on_completed_stage_is_rejected(self):
        self.r.save("job-a", "start", {})
        with self.assertRaises(StageDependencyError):
            self.r.save("job-a", "start", {"position": 9}, complete=False)

    def test_stage_completes_when_next_stage_is_saved(self):
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {"position": 5})
        point = self.r.resume("job-a")
        self.assertEqual(point["completed"], ["start", "fetch"])
        self.assertEqual(point["stage"], "parse")
        self.r.save("job-a", "parse", {"position": 5})
        point = self.r.resume("job-a")
        self.assertEqual(point["completed"], ["start", "fetch", "parse"])
        self.assertEqual(point["stage"], "store")

    def test_resume_unknown_job_starts_from_first_stage(self):
        point = self.r.resume("never-seen")
        self.assertFalse(point["restored"])
        self.assertEqual(point["stage"], DEFAULT_STAGES[0])
        self.assertEqual(point["version"], 0)
        self.assertEqual(point["stored_pages"], [])
        self.assertEqual(point["skipped"], [])


class ChecksumCorruptionTest(JobRecoveryBase):
    def _current_file(self, job):
        job_dir = self.job_dir(job)
        with open(os.path.join(job_dir, "current.json"), "rb") as handle:
            pointer = json.loads(handle.read().decode("utf-8"))
        return os.path.join(job_dir, pointer["file"])

    def test_current_checkpoint_corrupted_falls_back_to_previous_valid(self):
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {"position": 9})

        with open(self._current_file("job-a"), "wb") as handle:
            handle.write(b"{this is not json, write torn in half")

        point = self.r.resume("job-a")
        self.assertTrue(point["rollback"])
        self.assertTrue(point["restored"])
        self.assertEqual(point["version"], 1)
        self.assertEqual(point["stage"], "fetch")  # start 已完成，回退到 fetch 之前

    def test_tampered_payload_fails_checksum_and_rolls_back(self):
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {"position": 9})
        path = self._current_file("job-a")
        with open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
        payload["position"] = 999  # 篡改内容但不改校验和
        with open(path, "wb") as handle:
            handle.write(json.dumps(payload).encode("utf-8"))

        point = self.r.resume("job-a")
        self.assertTrue(point["rollback"])
        self.assertEqual(point["version"], 1)

    def test_torn_pointer_falls_back_by_scanning_generations(self):
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {"position": 2})
        with open(os.path.join(self.job_dir("job-a"), "current.json"), "wb") as handle:
            handle.write(b'{"file": "cp-0000')

        point = self.r.resume("job-a")
        self.assertTrue(point["rollback"])
        self.assertEqual(point["version"], 2)
        self.assertEqual(point["position"], 2)

    def test_all_checkpoints_corrupted_reports_rollback_and_fresh_start(self):
        self.r.save("job-a", "start", {"position": 1})
        job_dir = self.job_dir("job-a")
        for name in os.listdir(job_dir):
            if name.endswith(".json"):
                with open(os.path.join(job_dir, name), "wb") as handle:
                    handle.write(b"broken")
        point = self.r.resume("job-a")
        self.assertTrue(point["rollback"])
        self.assertFalse(point["restored"])
        self.assertEqual(point["stage"], DEFAULT_STAGES[0])
        self.assertIsNone(point["checksum"])

    def test_partial_write_leaves_previous_checkpoint_intact(self):
        self.r.save("job-a", "start", {})
        with mock.patch(
            "jobrecover.recover._atomic_write", side_effect=OSError("disk full")
        ):
            with self.assertRaises(CheckpointError):
                self.r.save("job-a", "fetch", {"position": 4})
        point = self.r.resume("job-a")
        self.assertFalse(point["rollback"])
        self.assertEqual(point["version"], 1)
        self.assertEqual(point["stage"], "fetch")  # 保存失败不推进阶段

    def test_unserializable_state_is_rejected_and_disk_untouched(self):
        self.r.save("job-a", "start", {})
        job_dir = self.job_dir("job-a")
        before = sorted(os.listdir(job_dir))
        with self.assertRaises(CheckpointError):
            self.r.save("job-a", "fetch", {"bad": {1, 2}})  # set 不可 JSON 序列化
        self.assertEqual(sorted(os.listdir(job_dir)), before)
        point = self.r.resume("job-a")
        self.assertEqual(point["stage"], "fetch")
        self.assertEqual(point["version"], 1)


class IsolationTest(JobRecoveryBase):
    def test_jobs_use_separate_directories_and_do_not_overwrite(self):
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {"position": 1})
        self.r.save("job-b", "start", {})
        self.r.save("job-b", "fetch", {})
        self.r.save("job-b", "parse", {"position": 7})

        self.assertTrue(os.path.isdir(self.job_dir("job-a")))
        self.assertTrue(os.path.isdir(self.job_dir("job-b")))

        point_a = self.r.resume("job-a")
        point_b = self.r.resume("job-b")
        self.assertEqual(point_a["position"], 1)
        self.assertEqual(point_b["position"], 7)
        self.assertEqual(point_a["completed"], ["start", "fetch"])
        self.assertEqual(point_b["completed"], ["start", "fetch", "parse"])

    def test_job_name_is_encoded_to_prevent_path_escape(self):
        self.r.save("../../etc/evil", "start", {})
        entries = os.listdir(self.base)
        self.assertEqual(len(entries), 1)  # 整个任务名被编码为单层目录
        self.assertNotIn(os.sep, entries[0])
        self.assertNotIn("/", entries[0])
        self.assertFalse(os.path.exists(os.path.join(self.base, "etc")))
        # 编码目录内仍能正常读回检查点
        point = self.r.resume("../../etc/evil")
        self.assertEqual(point["completed"], ["start"])

    def test_concurrent_saves_are_serialized_and_jobs_stay_isolated(self):
        errors = []

        def worker(job, pages):
            try:
                local = JobRecovery(self.base)  # 每个线程自己的实例
                local.save(job, "start", {})
                for page in range(1, pages + 1):
                    local.save(job, "fetch", {"position": page, "pages": list(range(1, page + 1))},
                               complete=False)
                local.save(job, "fetch", {"position": pages, "pages": list(range(1, pages + 1))})
            except Exception as exc:  # pragma: no cover - 失败时落到 errors
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("concurrent-a", 20))
        t2 = threading.Thread(target=worker, args=("concurrent-b", 10))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(errors, [])

        point_a = self.r.resume("concurrent-a")
        point_b = self.r.resume("concurrent-b")
        self.assertEqual(point_a["stored_pages"], list(range(1, 21)))
        self.assertEqual(point_b["stored_pages"], list(range(1, 11)))
        self.assertEqual(point_a["completed"], ["start", "fetch"])
        self.assertEqual(point_b["completed"], ["start", "fetch"])


class ResumeBehaviorTest(JobRecoveryBase):
    def _run_to_store(self):
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {"position": 2})
        self.r.save("job-a", "parse", {"position": 2})
        # 页面 1、2 已入库（逐页位点推进，store 仍为当前未完成阶段）
        self.r.save("job-a", "store", {"position": 1, "pages": [1]}, complete=False)
        self.r.save("job-a", "store", {"position": 2, "pages": [1, 2]}, complete=False)

    def test_resume_runs_only_unfinished_stages_and_skips_stored_pages(self):
        self._run_to_store()
        point = self.r.resume("job-a")
        self.assertEqual(point["stage"], "store")
        self.assertEqual(point["skipped"], ["start", "fetch", "parse"])
        self.assertEqual(point["stored_pages"], [1, 2])

        # 模拟重跑：已入库页面不再重复处理，只处理新页面
        remaining = [page for page in (1, 2, 3) if page not in point["stored_pages"]]
        self.assertEqual(remaining, [3])
        self.r.save("job-a", "store", {"position": 3, "pages": [1, 2, 3]},
                    complete=False)
        self.r.save("job-a", "store", {"position": 3, "pages": [1, 2, 3]})
        self.r.save("job-a", "finish", {"position": 3, "pages": [1, 2, 3]})

        done = self.r.resume("job-a")
        self.assertIsNone(done["stage"])
        self.assertTrue(done["finished"])
        self.assertEqual(done["completed"], list(DEFAULT_STAGES))

    def test_repeated_resume_returns_the_same_result(self):
        self._run_to_store()
        first = self.r.resume("job-a")
        second = self.r.resume("job-a")
        # 新实例（模拟新进程）读取同一份磁盘检查点，结果一致
        third = JobRecovery(self.base).resume("job-a")
        self.assertEqual(first, second)
        self.assertEqual(first, third)

    def test_stage_dependency_is_validated_on_save(self):
        with self.assertRaises(StageDependencyError):
            self.r.save("job-a", "parse", {})  # 跳过 start/fetch
        self.r.save("job-a", "start", {})
        self.r.save("job-a", "fetch", {})
        with self.assertRaises(StageDependencyError):
            self.r.save("job-a", "start", {})  # 后继阶段已落盘，逆序保存
        point = self.r.resume("job-a")
        self.assertEqual(point["completed"], ["start", "fetch"])
        self.assertEqual(point["stage"], "parse")  # fetch 已完成，下一阶段 parse


class LoggingAndTimingTest(JobRecoveryBase):
    def test_stage_elapsed_is_logged_and_checkpointed(self):
        records = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Handler()
        logger = logging.getLogger("jobrecover")
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            with self.r.stage("job-a", "start") as ctx:
                ctx.position = 0
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        messages = [record.getMessage() for record in records]
        self.assertTrue(any("enter stage=start" in msg for msg in messages))
        self.assertTrue(any("stage=start" in msg and "elapsed=" in msg for msg in messages))

        point = self.r.resume("job-a")
        self.assertIsInstance(point["stages"]["start"]["elapsed"], float)
        self.assertGreaterEqual(point["stages"]["start"]["elapsed"], 0.0)

    def test_failed_stage_does_not_advance(self):
        with self.assertRaises(RuntimeError):
            with self.r.stage("job-a", "start"):
                raise RuntimeError("boom mid-stage")
        point = self.r.resume("job-a")
        self.assertFalse(point["restored"])
        self.assertEqual(point["stage"], "start")


if __name__ == "__main__":
    unittest.main()
