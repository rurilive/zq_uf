import json
import subprocess
import sys
import unittest
from pathlib import Path

from timeoutgov import (
    TaskTimeoutError,
    dump_state,
    load_state,
    reset_state,
    run,
    set_clock,
    stats,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class Base(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.now = [0]
        set_clock(lambda: self.now[0])

    def work_after(self, seconds, value="ok"):
        def work():
            self.now[0] += seconds
            return value

        return work

    def seed_history(self, task_type, samples):
        for seconds in samples:
            run(
                {"name": f"seed-{task_type}-{seconds}", "type": task_type,
                 "work": self.work_after(seconds)},
                deadline=10_000,
            )


class DynamicTimeoutTest(Base):
    def test_history_lets_big_task_finish(self):
        self.seed_history("bulk", [40, 40, 40])
        result = run({"name": "big", "type": "bulk", "work": self.work_after(45)})
        self.assertEqual(result["result"], "ok")

    def test_small_task_killed_by_tight_limit(self):
        self.seed_history("tiny", [2, 2, 2])
        with self.assertRaises(TaskTimeoutError) as ctx:
            run({"name": "stuck", "type": "tiny", "work": self.work_after(10)})
        self.assertEqual(ctx.exception.limit, 3.0)
        self.assertEqual(ctx.exception.elapsed, 10)

    def test_explicit_deadline_overrides_dynamic(self):
        self.seed_history("bulk", [40, 40, 40])
        with self.assertRaises(TaskTimeoutError) as ctx:
            run({"name": "big", "type": "bulk", "work": self.work_after(10)}, deadline=5)
        self.assertEqual(ctx.exception.limit, 5.0)

    def test_unknown_type_uses_default_timeout(self):
        with self.assertRaises(TaskTimeoutError) as ctx:
            run({"name": "mystery", "type": "new-type", "work": self.work_after(31)})
        self.assertEqual(ctx.exception.limit, 30.0)

    def test_limit_clamped_to_minimum(self):
        self.seed_history("instant", [0, 0, 0])
        with self.assertRaises(TaskTimeoutError) as ctx:
            run({"name": "tiny-over", "type": "instant", "work": self.work_after(2)})
        self.assertEqual(ctx.exception.limit, 1.0)

    def test_timeout_error_is_timeout_error(self):
        with self.assertRaises(TimeoutError):
            run({"name": "compat", "type": "x", "work": self.work_after(31)})


class CheckpointTest(Base):
    def hanging_work(self, progress, units, hang):
        def work():
            for _ in range(units):
                self.now[0] += 1
                progress["done"] = progress.get("done", 0) + 1
            self.now[0] += hang
            return "unreachable"

        return work

    def test_timeout_saves_progress_and_stack(self):
        progress = {"done": 0}
        with self.assertRaises(TaskTimeoutError) as ctx:
            run({"name": "etl", "type": "batch", "progress": progress,
                 "work": self.hanging_work(progress, 3, 100)})
        saved = ctx.exception.checkpoint
        self.assertEqual(saved["progress"], {"done": 3})
        self.assertEqual(saved["task"], "etl")
        self.assertEqual(saved["last_frame"]["func"], "work")
        self.assertIn("test_dynamic.py", saved["stack"][-1])
        state = dump_state()
        self.assertEqual(state["checkpoints"]["etl"]["progress"], {"done": 3})
        json.dumps(state)

    def test_progress_must_be_dict(self):
        with self.assertRaises(TypeError):
            run({"name": "bad", "progress": "nope", "work": self.work_after(0)})

    def test_checkpoint_must_be_serializable(self):
        with self.assertRaises(ValueError):
            run({"name": "bad", "checkpoint": object(), "work": lambda cp: "x"})


class ResumeTest(Base):
    def test_resume_from_checkpoint(self):
        progress = {"done": 0}

        def hanging():
            for _ in range(3):
                self.now[0] += 1
                progress["done"] += 1
            self.now[0] += 100

        self.seed_history("batch", [5, 5, 5])
        with self.assertRaises(TaskTimeoutError) as ctx:
            run({"name": "etl", "type": "batch", "progress": progress, "work": hanging})
        saved = ctx.exception.checkpoint

        calls = []

        def resume_work(checkpoint):
            calls.append(checkpoint)
            done = checkpoint["progress"]["done"]
            while done < 10:
                self.now[0] += 1
                done += 1
            return f"done={done}"

        result = run({"name": "etl", "type": "batch",
                      "checkpoint": saved, "work": resume_work})
        self.assertTrue(result["resumed"])
        self.assertEqual(result["result"], "done=10")
        self.assertEqual(calls, [saved])
        self.assertEqual(stats()["pending_checkpoints"], [])
        recoveries = stats()["recoveries"]
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0]["name"], "etl")

    def test_checkpoint_but_work_takes_no_args(self):
        with self.assertRaises(ValueError):
            run({"name": "bad", "checkpoint": {"done": 1}, "work": lambda: "x"})


class StatsTest(Base):
    def test_stats_reports_timeouts_durations_recoveries(self):
        self.seed_history("tiny", [2, 4])
        with self.assertRaises(TaskTimeoutError):
            run({"name": "stuck", "type": "tiny", "work": self.work_after(50)})
        report = stats()
        self.assertEqual([t["name"] for t in report["timeouts"]], ["stuck"])
        dist = report["durations"]["tiny"]
        self.assertEqual(dist["count"], 2)
        self.assertEqual(dist["min"], 2)
        self.assertEqual(dist["max"], 4)
        self.assertEqual(dist["mean"], 3)
        self.assertEqual(report["recoveries"], [])
        self.assertEqual(report["pending_checkpoints"], ["stuck"])

    def test_state_roundtrip(self):
        self.seed_history("tiny", [2, 2])
        with self.assertRaises(TaskTimeoutError):
            run({"name": "stuck", "type": "tiny", "work": self.work_after(50)})
        blob = json.dumps(dump_state())
        reset_state()
        self.assertEqual(stats()["timeouts"], [])
        load_state(json.loads(blob))
        self.assertEqual([t["name"] for t in stats()["timeouts"]], ["stuck"])
        self.assertEqual(stats()["durations"]["tiny"]["count"], 2)
        self.assertEqual(stats()["pending_checkpoints"], ["stuck"])

    def test_load_state_rejects_bad_input(self):
        with self.assertRaises(TypeError):
            load_state([])
        with self.assertRaises(ValueError):
            load_state({"bogus": 1})
        with self.assertRaises(ValueError):
            load_state({"history": {"tiny": "not-a-list"}})


class ValidationTest(Base):
    def test_task_must_be_dict(self):
        with self.assertRaises(TypeError):
            run("not-a-task")

    def test_name_required(self):
        with self.assertRaises(ValueError):
            run({"work": self.work_after(0)})

    def test_work_must_be_callable(self):
        with self.assertRaises(TypeError):
            run({"name": "bad", "work": 42})

    def test_deadline_must_be_positive_number(self):
        with self.assertRaises(ValueError):
            run({"name": "bad", "work": self.work_after(0)}, deadline=0)
        with self.assertRaises(TypeError):
            run({"name": "bad", "work": self.work_after(0)}, deadline="30")


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        proc = subprocess.run(
            [sys.executable, "demo.py"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn("大任务完成", out)
        self.assertIn("已保存现场", out)
        self.assertIn("断点恢复", out)
        self.assertIn("超时任务: 1", out)
        self.assertIn("恢复结果: 1", out)


if __name__ == "__main__":
    unittest.main()
