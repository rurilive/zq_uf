import unittest

from taskqueue import Task, TaskQueue, TaskStatus


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TaskQueueTest(unittest.TestCase):
    def test_fifo_order(self):
        queue = TaskQueue()
        queue.push("a")
        queue.push("b")
        self.assertEqual(queue.lease("w1"), "a")
        self.assertEqual(queue.lease("w1"), "b")

    def test_empty_returns_none(self):
        self.assertIsNone(TaskQueue().lease("w1"))

    def test_complete_records_task(self):
        queue = TaskQueue()
        queue.push("a")
        task = queue.lease("w1")
        queue.complete(task)
        self.assertEqual(queue.done, ["a"])

    def test_priority_order_keeps_fifo_within_same_priority(self):
        queue = TaskQueue()
        queue.push("normal-1", priority=10)
        queue.push("urgent", priority=1)
        queue.push("normal-2", priority=10)

        self.assertEqual(queue.lease("w1"), "urgent")
        self.assertEqual(queue.lease("w1"), "normal-1")
        self.assertEqual(queue.lease("w1"), "normal-2")

    def test_lease_expiry_requeues_and_reuses_attempt_limit(self):
        clock = FakeClock()
        queue = TaskQueue(
            default_lease_timeout=5,
            default_max_attempts=2,
            clock=clock,
        )
        queue.push("stuck")

        first = queue.lease("w1", lease_timeout=5)
        self.assertEqual(queue.get_status(first), TaskStatus.RUNNING)

        clock.advance(4)
        queue.renew_lease(first, "w1", lease_timeout=5)
        clock.advance(4)
        self.assertIsNone(queue.lease("w2"))

        clock.advance(1)
        second = queue.lease("w2")
        self.assertEqual(second, "stuck")
        self.assertEqual(queue.get_status(first), TaskStatus.RUNNING)
        self.assertEqual(queue.get_task(first).attempts, 2)
        clock.advance(6)
        reclaimed = queue.reclaim_expired()

        self.assertEqual(reclaimed, [])
        self.assertEqual(queue.get_status(first), TaskStatus.DEAD_LETTER)
        self.assertEqual(queue.dead_letter, ["stuck"])

    def test_state_transitions_for_success_failure_and_retry(self):
        queue = TaskQueue(default_max_attempts=2)
        queue.push("retry")
        retry_task = queue.lease("w1")

        self.assertEqual(queue.get_status(retry_task), TaskStatus.RUNNING)
        queue.fail(retry_task, "temporary error")
        self.assertEqual(queue.get_status(retry_task), TaskStatus.PENDING)
        retry_task = queue.lease("w1")
        queue.fail(retry_task, "retry exhausted")
        self.assertEqual(queue.get_status(retry_task), TaskStatus.DEAD_LETTER)

        queue.push("failed")
        failed_task = queue.lease("w1")
        queue.fail(failed_task, "permanent error", retry=False)
        self.assertEqual(queue.get_status(failed_task), TaskStatus.FAILED)
        self.assertEqual(queue.failed, ["failed"])

        queue.push("success")
        success_task = queue.lease("w1")
        queue.complete(success_task)
        self.assertEqual(queue.get_status(success_task), TaskStatus.SUCCESS)
        self.assertEqual(queue.done, ["success"])

    def test_worker_cannot_complete_or_renew_another_workers_lease(self):
        queue = TaskQueue()
        queue.push("owned")
        task = queue.lease("owner")

        with self.assertRaises(RuntimeError):
            queue.complete(task, worker="other")
        with self.assertRaises(RuntimeError):
            queue.renew_lease(task, "other")

    def test_flows_are_isolated(self):
        queue = TaskQueue()
        queue.push("billing-a", flow_name="billing")
        queue.push("billing-b", priority=1, flow_name="billing")
        queue.push("shipping", flow_name="shipping")

        self.assertEqual(queue.lease("w1", "billing"), "billing-b")
        self.assertEqual(queue.lease("w2", "shipping"), "shipping")
        self.assertIsNone(queue.lease("w3", "shipping"))
        self.assertEqual(queue.lease("w1", "billing"), "billing-a")
        self.assertEqual(queue.tasks("billing").__len__(), 2)
        self.assertEqual(queue.stats("shipping"), {
            TaskStatus.PENDING.value: 0,
            TaskStatus.RUNNING.value: 1,
            TaskStatus.SUCCESS.value: 0,
            TaskStatus.FAILED.value: 0,
            TaskStatus.DEAD_LETTER.value: 0,
        })

    def test_failed_task_requeues_after_other_same_priority_pending_tasks(self):
        queue = TaskQueue()
        queue.push("first")
        queue.push("second")
        first = queue.lease("w1")
        queue.fail(first)

        self.assertEqual(queue.pending, ["second", "first"])

    def test_push_task_object_and_reject_non_pending_task(self):
        queue = TaskQueue()
        task = Task(task_id="t1", payload={"id": 1})
        queue.push(task)

        self.assertEqual(queue.lease("w1"), {"id": 1})
        with self.assertRaises(ValueError):
            queue.push(Task(task_id="t2", payload="x", status=TaskStatus.RUNNING))

    def test_invalid_max_attempts_is_rejected(self):
        queue = TaskQueue()
        with self.assertRaises(ValueError):
            queue.push("invalid", max_attempts=0)
