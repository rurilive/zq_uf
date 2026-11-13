import unittest

from flowstep import (
    Branch,
    FlowDefinitionError,
    Registry,
    STATE_KEY,
    Step,
    dumps,
    fail,
    from_dict,
    loads,
    run,
    to_dict,
)


class FlowstepTest(unittest.TestCase):
    def test_steps_share_context(self):
        def first(ctx):
            return {"a": 1}

        def second(ctx):
            return {"b": ctx["a"] + 1}

        self.assertEqual(run([first, second], {}), {"a": 1, "b": 2})

    def test_empty_flow(self):
        self.assertEqual(run([], {"x": 1}), {"x": 1})

    def test_input_context_not_mutated(self):
        context = {"x": 1}
        run([lambda ctx: {"y": 2}], context)
        self.assertEqual(context, {"x": 1})


class ResumeTest(unittest.TestCase):
    """断点续跑：非幂等步骤只执行一次，失败后从断点继续。"""

    def setUp(self):
        self.reg = Registry()
        self.calls = {"reserve": 0, "check": 0, "notify": 0}

        def reserve(ctx):
            self.calls["reserve"] += 1
            return {"order_no": "O-1"}

        def check(ctx):
            self.calls["check"] += 1
            if ctx["amount"] > 1000:
                fail("BUDGET_EXCEEDED", field="amount", message="超出预算")
            return {"approved": True}

        def notify(ctx):
            self.calls["notify"] += 1
            return {"notified": True}

        self.reg.register(reserve)
        self.reg.register(check)
        self.reg.register(notify)
        self.flow = [
            Step("reserve", "reserve", outputs=["order_no"], idempotent=False),
            Step("check", "check", inputs=["amount"], outputs=["approved"],
                 idempotent=True),
            Step("notify", "notify", outputs=["notified"], idempotent=False),
        ]

    def test_continue_from_failed_step(self):
        first = run(self.flow, {"amount": 1500}, registry=self.reg)
        self.assertFalse(first.ok)
        self.assertEqual(first.failed_step, "check")
        self.assertEqual(first.code, "BUDGET_EXCEEDED")
        self.assertEqual(first.field, "amount")
        self.assertEqual(first.completed_steps, ["reserve"])
        self.assertEqual(self.calls, {"reserve": 1, "check": 1, "notify": 0})

        second = run(self.flow, {**first, "amount": 500}, registry=self.reg)
        self.assertTrue(second.ok)
        self.assertEqual(second["order_no"], "O-1")
        self.assertEqual(second["approved"], True)
        self.assertEqual(second["notified"], True)
        self.assertEqual(self.calls, {"reserve": 1, "check": 2, "notify": 1})
        self.assertEqual(second.completed_steps, ["reserve", "notify"])

    def test_checkpoint_survives_serialization(self):
        first = run(self.flow, {"amount": 1500}, registry=self.reg)
        import json

        rebuilt = loads(dumps(self.flow))
        resumed_context = {**first, "amount": 500}
        resumed_context[STATE_KEY] = json.loads(json.dumps(first[STATE_KEY]))
        second = run(rebuilt, resumed_context, registry=self.reg)
        self.assertTrue(second.ok)
        self.assertEqual(self.calls["reserve"], 1)


class IdempotentTest(unittest.TestCase):
    """幂等步骤每次重跑都执行，非幂等步骤凭标记跳过。"""

    def test_idempotent_reruns_non_idempotent_skips(self):
        reg = Registry()
        calls = {"read": 0, "write": 0}

        def read_config(ctx):
            calls["read"] += 1
            return {"config": {"retries": 3}}

        def write_record(ctx):
            calls["write"] += 1
            return {"record_id": "R-1"}

        reg.register(read_config)
        reg.register(write_record)
        flow = [
            Step("read_config", "read_config", outputs=["config"], idempotent=True),
            Step("write_record", "write_record", outputs=["record_id"],
                 idempotent=False),
        ]

        first = run(flow, {}, registry=reg)
        second = run(flow, first, registry=reg)
        self.assertTrue(second.ok)
        self.assertEqual(calls, {"read": 2, "write": 1})
        self.assertEqual(first.completed_steps, ["write_record"])


class ErrorLocationTest(unittest.TestCase):
    """错误定位到业务步骤、错误码与字段名，而不是裸行号。"""

    def test_business_error_carries_step_code_and_field(self):
        reg = Registry()

        def submit(ctx):
            if not ctx.get("email"):
                fail("EMAIL_REQUIRED", field="email")
            return {"submitted": True}

        reg.register(submit)
        result = run([Step("submit", "submit", outputs=["submitted"])], {},
                     registry=reg)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, {
            "step": "submit",
            "code": "EMAIL_REQUIRED",
            "field": "email",
            "message": "EMAIL_REQUIRED",
        })
        self.assertEqual(result.failed_step, "submit")
        self.assertNotIn("submitted", result)

    def test_missing_input_is_reported_with_field(self):
        reg = Registry()
        def use_token(ctx):
            return {}

        reg.register(use_token)
        flow = [Step("need_token", "use_token", inputs=["token"])]
        result = run(flow, {}, registry=reg)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "INPUT_MISSING")
        self.assertEqual(result.field, "token")

    def test_unexpected_exception_is_located(self):
        reg = Registry()

        def boom(ctx):
            raise RuntimeError("db down")

        reg.register(boom)
        result = run([Step("boom", "boom")], {}, registry=reg)
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_step, "boom")
        self.assertEqual(result.code, "STEP_RAISED")
        self.assertIn("db down", result.error["message"])


class BranchTest(unittest.TestCase):
    """条件分支与步骤级 when，全部由纯数据条件驱动。"""

    def _registry(self):
        reg = Registry()
        reg.register(lambda ctx: {"route": "fast"}, name="fast_path")
        reg.register(lambda ctx: {"route": "slow"}, name="slow_path")
        reg.register(lambda ctx: {"archived": True}, name="archive")
        return reg

    def test_branch_picks_matching_route(self):
        reg = self._registry()
        flow = [
            Branch([
                ({"field": "level", "op": "gt", "value": 5},
                 [Step("fast_path", "fast_path", outputs=["route"])]),
                (None,
                 [Step("slow_path", "slow_path", outputs=["route"])]),
            ])
        ]
        self.assertEqual(run(flow, {"level": 9}, registry=reg)["route"], "fast")
        self.assertEqual(run(flow, {"level": 1}, registry=reg)["route"], "slow")

    def test_step_when_skips_and_re_evaluates_on_rerun(self):
        reg = self._registry()
        flow = [
            Step("archive", "archive", outputs=["archived"],
                 when={"field": "done", "op": "truthy"}),
        ]
        skipped = run(flow, {"done": False}, registry=reg)
        self.assertTrue(skipped.ok)
        self.assertNotIn("archived", skipped)
        self.assertEqual(skipped.completed_steps, [])

        resumed = run(flow, {**skipped, "done": True}, registry=reg)
        self.assertTrue(resumed["archived"])
        self.assertEqual(resumed.completed_steps, ["archive"])

    def test_duplicate_step_name_rejected(self):
        reg = self._registry()
        flow = [
            Step("fast_path", "fast_path"),
            Step("fast_path", "fast_path"),
        ]
        with self.assertRaises(FlowDefinitionError):
            run(flow, {}, registry=reg)


class SerializationTest(unittest.TestCase):
    def test_flow_definition_round_trips_as_json(self):
        flow = [
            Step("start", "start", outputs=["ticket_id"], idempotent=True),
            Branch([
                ({"field": "ticket_id", "op": "exists"},
                 [Step("archive", "archive", when={"field": "done", "op": "eq",
                                                    "value": True})]),
            ]),
        ]
        encoded = dumps(flow)
        restored = loads(encoded)
        self.assertEqual(to_dict(restored), to_dict(flow))
        self.assertIsInstance(from_dict(to_dict(flow)), list)
        self.assertIn("branch", encoded)
