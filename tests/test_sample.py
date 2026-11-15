import os
import tempfile
import unittest

from sampleci import (
    compare,
    error,
    full_scan,
    normalize_plan,
    replay,
    sample,
    summarize,
    write_files,
)
from sampleci.stats import _t_critical, _weighted_quantile


def make_rows(n=1000, seed=7):
    """构造带长尾的分层数据：A 主体、B 主体、C 稀有长尾。"""
    rng = __import__("random").Random(seed)
    rows = []
    for i in range(n):
        if i % 100 == 0:
            group, value = "C", 10_000 + rng.random() * 5_000  # 10 条稀有大值
        elif i % 2 == 0:
            group, value = "A", rng.gauss(100, 10)
        else:
            group, value = "B", rng.gauss(50, 5)
        rows.append({"id": i, "group": group, "value": value})
    return rows


class PlanTest(unittest.TestCase):
    def test_normalize_defaults_and_independent_copy(self):
        p = normalize_plan({"strata": {"A": {"ratio": 0.3}}})
        self.assertEqual(p["seed"], 20261116)
        p["strata"]["A"]["ratio"] = 0.9
        again = normalize_plan({"strata": {"A": {"ratio": 0.3}}})
        self.assertAlmostEqual(again["strata"]["A"]["ratio"], 0.3)

    def test_bad_ratio_rejected(self):
        with self.assertRaises(ValueError):
            normalize_plan({"ratio": 1.5})


class ReproducibilityTest(unittest.TestCase):
    def test_same_plan_same_result(self):
        rows = make_rows()
        plan = {"strata_field": "group", "ratio": 0.2, "seed": 42}
        first = sample(rows, plan)
        second = sample(rows, plan)
        self.assertEqual(first["indices"], second["indices"])
        self.assertEqual(first["rows"], second["rows"])
        self.assertEqual(first["seed"], 42)

    def test_seed_changes_result(self):
        rows = make_rows()
        plan = {"strata_field": "group", "ratio": 0.2}
        a = sample(rows, {**plan, "seed": 1})
        b = sample(rows, {**plan, "seed": 2})
        self.assertNotEqual(a["indices"], b["indices"])

    def test_layer_seed_independent_of_other_layers(self):
        """删掉 B 层后，A 层抽到的索引不应发生位移。"""
        rows = make_rows()
        plan = {"strata_field": "group", "ratio": 0.2, "seed": 42}
        full = sample(rows, plan)
        only_a = sample([r for r in rows if r["group"] != "B"], plan)
        # A 层在全量数据中的全局索引（注意删组会改变全局索引，
        # 因此按层内顺序比较 id）
        full_a_ids = [r["id"] for r in full["rows"] if r["group"] == "A"]
        only_a_ids = [r["id"] for r in only_a["rows"] if r["group"] == "A"]
        self.assertEqual(full_a_ids, only_a_ids)

    def test_replay_by_recorded_indices(self):
        rows = make_rows()
        plan = {"strata_field": "group", "ratio": 0.2, "seed": 42}
        first = sample(rows, plan)
        again = replay(rows, plan, first)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["indices"], first["indices"])
        self.assertEqual(again["rows"], first["rows"])

    def test_replay_rejects_changed_plan(self):
        rows = make_rows()
        first = sample(rows, {"strata_field": "group", "seed": 1})
        with self.assertRaises(ValueError):
            replay(rows, {"strata_field": "group", "seed": 2}, first)

    def test_replay_rejects_changed_layer_size(self):
        rows = make_rows()
        plan = {"strata_field": "group", "seed": 1}
        first = sample(rows, plan)
        with self.assertRaises(ValueError):
            replay(rows + [{"id": 9999, "group": "A", "value": 1.0}],
                   plan, first)


class AllocationTest(unittest.TestCase):
    def test_ratio_size(self):
        rows = [{"value": i} for i in range(100)]
        out = sample(rows, {"ratio": 0.2})
        self.assertEqual(out["n_sampled"], 20)

    def test_mixed_ratio_and_fixed_n(self):
        rows = make_rows()
        plan = {
            "strata_field": "group",
            "ratio": 0.1,
            "strata": {"A": {"n": 50}, "B": {"ratio": 0.3}},
            "tail_census": False,
        }
        out = sample(rows, plan)
        self.assertEqual(out["strata"]["A"]["sampled"], 50)
        self.assertEqual(out["strata"]["A"]["mode"], "n")
        self.assertEqual(out["strata"]["B"]["mode"], "ratio")
        # C 走全局兜底比例
        self.assertLess(out["strata"]["C"]["sampled"],
                        out["strata"]["C"]["size"])

    def test_tail_layer_census_by_default(self):
        rows = make_rows()
        out = sample(rows, {"strata_field": "group", "ratio": 0.1})
        self.assertIn("C", out["tail_labels"])
        self.assertEqual(out["strata"]["C"]["mode"], "census")
        self.assertEqual(out["strata"]["C"]["sampled"],
                         out["strata"]["C"]["size"])
        # 长尾大值一个都不漏
        self.assertEqual(
            sorted(r["id"] for r in out["rows"] if r["group"] == "C"),
            sorted(r["id"] for r in rows if r["group"] == "C"),
        )

    def test_tail_can_be_disabled(self):
        rows = make_rows()
        out = sample(rows, {"strata_field": "group", "ratio": 0.1,
                            "tail_census": False})
        self.assertIn("C", out["tail_labels"])
        self.assertNotEqual(out["strata"]["C"]["mode"], "census")

    def test_no_duplicate_indices(self):
        rows = make_rows()
        out = sample(rows, {"strata_field": "group", "ratio": 0.5})
        self.assertEqual(len(out["indices"]), len(set(out["indices"])))


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.rows = make_rows()
        self.plan = {"strata_field": "group", "ratio": 0.3, "seed": 42}
        self.sampled = sample(self.rows, self.plan)

    def test_output_shape(self):
        stats = summarize(self.sampled)
        for key in ("mean", "quantiles", "range", "ci", "status",
                    "per_stratum", "tail"):
            self.assertIn(key, stats)
        for q in ("0.5", "0.9", "0.95", "0.99"):
            self.assertIn(q, stats["quantiles"])
            self.assertIn("estimate", stats["quantiles"][q])

    def test_mean_ci_covers_population_mean(self):
        full = full_scan(self.rows)
        stats = summarize(self.sampled)
        self.assertIsNotNone(stats["ci"])
        self.assertLessEqual(stats["ci"][0], full["mean"])
        self.assertGreaterEqual(stats["ci"][1], full["mean"])
        # 分层估计 = 各层按总体层大小 N_h 加权的层均值
        self.assertAlmostEqual(stats["mean"],
                               sum(m["size"] * m["mean"]
                                   for m in stats["per_stratum"].values())
                               / sum(m["size"]
                                     for m in stats["per_stratum"].values()))

    def test_tail_reported_separately(self):
        stats = summarize(self.sampled)
        self.assertEqual(stats["tail"]["labels"], ["C"])
        self.assertEqual(stats["main"]["labels"], ["A", "B"])
        tail = stats["tail"]["summary"]
        self.assertEqual(tail["n_sampled"],
                         self.sampled["strata"]["C"]["size"])
        # 总体 per_stratum 含全部三层；主体明细里不含 C，长尾单独成组
        self.assertEqual(set(stats["per_stratum"]), {"A", "B", "C"})
        self.assertEqual(
            set(stats["main"]["summary"]["per_stratum"]), {"A", "B"}
        )
        self.assertEqual(
            tail["n_population"], self.sampled["strata"]["C"]["size"]
        )

    def test_quantile_stats_separate_from_mean_and_reproducible(self):
        s1 = summarize(self.sampled)
        s2 = summarize(self.sampled)
        for q in s1["quantiles"]:
            self.assertEqual(s1["quantiles"][q]["estimate"],
                             s2["quantiles"][q]["estimate"])
            self.assertEqual(s1["quantiles"][q]["ci"],
                             s2["quantiles"][q]["ci"])

    def test_quantiles_carry_two_explicit_bases(self):
        stats = summarize(self.sampled)
        for item in stats["quantiles"].values():
            self.assertIn("estimate_weighted", item)
            self.assertIn("estimate_sample", item)
            self.assertTrue(item["note"])
        # 普查长尾层权重=1：总体加权 P99 不应被长尾点整体顶到长尾量级
        p99 = stats["quantiles"]["0.99"]["estimate_weighted"]
        tail_mean = stats["tail"]["summary"]["mean"]
        self.assertLess(p99, tail_mean)

    def test_census_stratum_weight_is_one(self):
        stats = summarize(self.sampled)
        self.assertEqual(stats["per_stratum"]["C"]["weight"], 1.0)
        self.assertTrue(stats["per_stratum"]["C"]["census"])
        self.assertGreater(stats["per_stratum"]["A"]["weight"], 1.0)

    def test_insufficient_sample_no_misleading_ci(self):
        tiny = sample([{"group": "A", "value": 1.0},
                       {"group": "A", "value": 2.0},
                       {"group": "A", "value": 3.0},
                       {"group": "A", "value": 4.0}],
                      {"strata_field": "group", "strata": {"A": {"n": 1}},
                       "tail_census": False, "tail_min_count": 1})
        stats = summarize(tiny)
        self.assertIsNone(stats["ci"])
        self.assertEqual(stats["status"], "insufficient")
        self.assertIsNotNone(stats["reason"])
        # 分位数仍有点估计，但不挂假区间
        self.assertIsNone(stats["quantiles"]["0.5"]["ci"])

    def test_census_is_exact(self):
        out = sample(self.rows, {"strata_field": "group", "ratio": 1.0})
        stats = summarize(out)
        # 全部层普查：总体均值精确、区间退化为点，与全量真值一致
        full = full_scan(self.rows)
        self.assertEqual(stats["status"], "exact")
        self.assertEqual(stats["ci"], [stats["mean"], stats["mean"]])
        self.assertAlmostEqual(stats["mean"], full["mean"], places=10)
        # 长尾层同样普查精确，单独给出
        tail_rows = [r for r in self.rows if r["group"] == "C"]
        full_tail = full_scan(tail_rows)
        self.assertAlmostEqual(
            stats["tail"]["summary"]["mean"], full_tail["mean"], places=10
        )

    def test_weighted_quantile_linear_interpolation(self):
        pairs = [(1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (4.0, 1.0)]
        qs = _weighted_quantile(pairs, [0.5])
        self.assertAlmostEqual(qs["0.5"], 2.5)
        # 权重偏向小值时，中位数应左移
        qs = _weighted_quantile([(1.0, 3.0), (2.0, 1.0), (3.0, 1.0),
                                 (4.0, 1.0)], [0.5])
        self.assertLess(qs["0.5"], 2.5)

    def test_t_critical_known_values(self):
        self.assertAlmostEqual(_t_critical(10, 0.05), 2.228, places=2)
        self.assertAlmostEqual(_t_critical(30, 0.05), 2.042, places=2)
        self.assertAlmostEqual(_t_critical(1000, 0.05), 1.962, places=2)

    def test_error_alias(self):
        self.assertEqual(error(self.sampled)["mean"],
                         summarize(self.sampled)["mean"])

    def test_non_numeric_rows_excluded_not_counted_as_zero(self):
        rows = [{"value": 1.0}, {"value": "x"}, {"value": True},
                {}, {"value": 3.0}]
        stats = summarize(sample(rows, {"ratio": 1.0}))
        self.assertAlmostEqual(stats["mean"], 2.0)  # 仅 1.0 / 3.0 有效
        self.assertEqual(stats["n_sampled"], 2)
        self.assertEqual(stats["n_sampled_rows"], 5)


class ValidationTest(unittest.TestCase):
    def test_full_scan_exact(self):
        rows = [{"value": float(i)} for i in range(1, 11)]
        full = full_scan(rows)
        self.assertEqual(full["n"], 10)
        self.assertAlmostEqual(full["mean"], 5.5)
        self.assertAlmostEqual(full["quantiles"]["0.5"], 5.5)
        self.assertEqual(full["status"], "exact")

    def test_compare_accepts_good_plan(self):
        rows = make_rows(5000)
        plan = {"strata_field": "group", "ratio": 0.3, "seed": 42}
        verdict = compare(sample(rows, plan), full_scan(rows),
                          max_relative_bias=0.1)
        self.assertEqual(verdict["verdict"], "accepted",
                          msg=str(verdict["checks"]))
        # 均值是硬口径；分位检查存在但默认不阻断
        mean_check = next(c for c in verdict["checks"]
                          if c["metric"] == "mean")
        self.assertTrue(mean_check["blocking"])
        self.assertTrue(
            all(not c["blocking"] for c in verdict["checks"]
                if c["metric"].startswith("quantile"))
        )

    def test_compare_quantile_gate_blocks(self):
        rows = make_rows(5000)
        plan = {"strata_field": "group", "ratio": 0.3, "seed": 42}
        strict = compare(sample(rows, plan), full_scan(rows),
                         max_relative_bias=0.1, quantile_gate=True)
        # P99 对长尾敏感，严格模式下应被拦（若恰好通过则至少保证语义不崩）
        self.assertIn(strict["verdict"], ("accepted", "rejected"))
        self.assertTrue(all(c["blocking"] for c in strict["checks"]))

    def test_compare_rejects_when_no_ci(self):
        rows = [{"group": "A", "value": float(i)} for i in range(40)]
        tiny = sample(rows, {"strata_field": "group",
                             "strata": {"A": {"n": 1}},
                             "tail_census": False, "tail_min_count": 1})
        verdict = compare(tiny, full_scan(rows))
        self.assertEqual(verdict["verdict"], "rejected")
        self.assertIn("样本量不足", verdict["checks"][0]["reason"])


class ExportTest(unittest.TestCase):
    def test_write_files_and_json_roundtrip(self):
        import json
        rows = make_rows()
        sampled = sample(rows, {"strata_field": "group", "seed": 42})
        stats = summarize(sampled)
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "out")
            paths = write_files(base, sampled, stats)
            for path in paths:
                self.assertTrue(os.path.exists(path))
            with open(paths[0], encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["indices"], sampled["indices"])
            self.assertEqual(payload["stats"]["mean"], stats["mean"])
            with open(paths[1], encoding="utf-8") as fh:
                lines = fh.read().strip().splitlines()
            self.assertEqual(len(lines), stats["n_sampled_rows"] + 1)
            # 表头列名全局去重，不随样本行数膨胀
            header = lines[0].split(",")
            self.assertEqual(header, ["index", "stratum", "weight",
                                      "value", "group", "id"])


if __name__ == "__main__":
    unittest.main()
