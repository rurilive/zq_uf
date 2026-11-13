import unittest

from schemacheck import validate


class SchemacheckTest(unittest.TestCase):
    def test_required_passes(self):
        rows = [{"id": "1", "name": "a"}]
        result = validate(rows, {"required": ["id", "name"]})
        self.assertEqual(result["ok"], rows)
        self.assertEqual(result["bad"], [])

    def test_missing_field_rejected(self):
        rows = [{"id": "1"}, {"id": "2", "name": "b"}]
        result = validate(rows, {"required": ["id", "name"]})
        self.assertEqual(result["bad"][0]["row"], 0)
        self.assertEqual(result["bad"][0]["missing"], ["name"])

    def test_empty_rows(self):
        self.assertEqual(validate([], {"required": ["id"]}), {"ok": [], "bad": []})


class RuleKindsTest(unittest.TestCase):
    def test_type(self):
        rules = {"fields": {"age": {"type": "int"}}}
        result = validate([{"age": "30"}, {"age": "abc"}], rules)
        self.assertEqual(len(result["ok"]), 1)
        bad = result["bad"][0]
        self.assertEqual(bad["row"], 1)
        self.assertEqual(bad["field"], "age")
        self.assertEqual(bad["value"], "abc")
        self.assertIn("int", bad["reason"])

    def test_length(self):
        rules = {"fields": {"name": {"length": {"min": 2, "max": 4}}}}
        result = validate([{"name": "ab"}, {"name": "a"}, {"name": "abcde"}], rules)
        self.assertEqual(len(result["ok"]), 1)
        self.assertEqual([e["row"] for e in result["bad"]], [1, 2])
        self.assertEqual(result["bad"][0]["value"], "a")
        self.assertIn("min", result["bad"][0]["reason"])
        self.assertIn("max", result["bad"][1]["reason"])

    def test_enum(self):
        rules = {"fields": {"role": {"enum": ["admin", "user"]}}}
        result = validate([{"role": "admin"}, {"role": "root"}], rules)
        self.assertEqual(len(result["ok"]), 1)
        self.assertEqual(result["bad"][0]["value"], "root")
        self.assertIn("enum", result["bad"][0]["reason"])

    def test_regex(self):
        rules = {"fields": {"code": {"regex": r"[A-Z]{2}\d{2}"}}}
        result = validate([{"code": "AB12"}, {"code": "ab12"}], rules)
        self.assertEqual(len(result["ok"]), 1)
        self.assertEqual(result["bad"][0]["field"], "code")
        self.assertEqual(result["bad"][0]["value"], "ab12")

    def test_cross_field(self):
        rules = {"checks": [
            {"id": "order", "cross_field": {"left": "start", "op": "<=", "right": "end"}},
        ]}
        rows = [
            {"start": "2024-01-01", "end": "2024-06-01"},
            {"start": "2024-07-01", "end": "2024-01-01"},
        ]
        result = validate(rows, rules)
        self.assertEqual(result["ok"], [rows[0]])
        self.assertEqual(result["bad"][0]["row"], 1)
        self.assertEqual(result["bad"][0]["field"], "start")
        self.assertEqual(result["bad"][0]["value"], "2024-07-01")

    def test_reference(self):
        rules = {
            "fields": {"dept": {"reference": {"dataset": "depts", "key": "id"}}},
            "datasets": {"depts": [{"id": "D1"}, {"id": "D2"}]},
        }
        result = validate([{"dept": "D1"}, {"dept": "D9"}], rules)
        self.assertEqual(len(result["ok"]), 1)
        self.assertEqual(result["bad"][0]["value"], "D9")
        self.assertIn("depts", result["bad"][0]["reason"])


class DependencyTest(unittest.TestCase):
    def test_dependency_skip(self):
        rules = {"fields": {
            "age": {"type": "int"},
            "email": {"regex": r"[^@]+@[^@]+", "depends_on": ["age"]},
        }}
        rows = [{"age": "x", "email": "not-an-email"}]
        result = validate(rows, rules)
        self.assertEqual(len(result["bad"]), 1)
        self.assertEqual(result["bad"][0]["field"], "age")
        self.assertEqual(result["skipped"][0]["check"], "email")
        self.assertTrue(result["skipped"][0]["skipped"])
        self.assertIn("age", result["skipped"][0]["reason"])

    def test_dependency_passes_when_prerequisite_ok(self):
        rules = {"fields": {
            "age": {"type": "int"},
            "email": {"regex": r"[^@]+@[^@]+", "depends_on": ["age"]},
        }}
        result = validate([{"age": "30", "email": "a@b.com"}], rules)
        self.assertEqual(len(result["ok"]), 1)
        self.assertNotIn("skipped", result)


class BudgetTest(unittest.TestCase):
    RULES = {
        "dataset": "users",
        "fields": {"age": {"type": "int"}},
        "budget": {"users": 0.5, "default": 0.0},
    }
    ROWS = [{"age": "1"}, {"age": "2"}, {"age": "3"}, {"age": "x"}]

    def test_under_budget_isolates_bad_rows(self):
        result = validate(self.ROWS, self.RULES)
        self.assertEqual(len(result["ok"]), 3)
        self.assertEqual(len(result["bad"]), 1)
        self.assertFalse(result["budget"]["exceeded"])
        self.assertEqual(result["budget"]["dataset"], "users")
        self.assertAlmostEqual(result["budget"]["error_rate"], 0.25)
        self.assertNotIn("batch_rejected", result)

    def test_over_budget_rejects_batch(self):
        rules = dict(self.RULES, budget={"users": 0.1})
        result = validate(self.ROWS, rules)
        self.assertTrue(result["batch_rejected"])
        self.assertEqual(result["ok"], [])
        self.assertTrue(result["budget"]["exceeded"])
        self.assertEqual(len(result["bad"]), 1)

    def test_per_dataset_budget_fallback(self):
        rules = dict(self.RULES, dataset="orders")
        result = validate([{"age": "x"}], rules)
        self.assertTrue(result["budget"]["exceeded"])
        self.assertEqual(result["budget"]["max_error_rate"], 0.0)


class StreamTest(unittest.TestCase):
    def test_validate_accepts_generator(self):
        rows = ({"age": str(i)} for i in range(5))
        result = validate(rows, {"fields": {"age": {"type": "int"}}})
        self.assertEqual(len(result["ok"]), 5)

    def test_validate_stream_is_lazy(self):
        from schemacheck import validate_stream
        consumed = []

        def source():
            for i in range(100):
                consumed.append(i)
                yield {"age": str(i) if i != 1 else "x"}

        events = validate_stream(source(), {"fields": {"age": {"type": "int"}}})
        kind, payload = next(events)
        self.assertEqual(kind, "ok")
        self.assertLess(len(consumed), 5)
        rest = list(events)
        self.assertEqual(rest[-1][0], "summary")
        self.assertEqual(rest[-1][1]["bad_rows"], 1)
        bad = [p for k, p in rest if k == "bad"]
        self.assertEqual(bad[0]["row"], 1)
        self.assertEqual(bad[0]["value"], "x")


class ReplayTest(unittest.TestCase):
    def test_replay_rejected_samples(self):
        from schemacheck import replay
        rules = {"fields": {"age": {"type": "int"}}}
        rows = [{"age": "1"}, {"age": "x"}, {"age": "y"}]
        result = validate(rows, rules)
        again = replay(result["bad"], rules)
        self.assertEqual(again["ok"], [])
        self.assertEqual(len(again["bad"]), 2)
        self.assertEqual(
            [e["value"] for e in again["bad"]],
            [e["value"] for e in result["bad"]],
        )
        self.assertEqual(
            [e["reason"] for e in again["bad"]],
            [e["reason"] for e in result["bad"]],
        )
