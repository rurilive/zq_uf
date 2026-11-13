import json
import os
import tempfile
import unittest

from filerouter import load_rules, match_rule, read_reason, retry_pending, route


class FilerouterTest(unittest.TestCase):
    def test_route_by_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.log")
            open(src, "w").write("x")
            out = route(src, os.path.join(tmp, "out"))
            self.assertTrue(out.endswith(os.path.join("log", "a.log")))


def _write(path, content):
    with open(path, "w") as f:
        f.write(content)


class RuleMatchTest(unittest.TestCase):
    def test_multi_condition_match(self):
        rules = [
            {"name": "app-error", "suffix": "log", "prefix": "app",
             "min_size": 10, "contains": "ERROR"},
            {"name": "log", "suffix": "log"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            hit = os.path.join(tmp, "app_1.log")
            _write(hit, "xxxxxxERROR")  # 前缀、大小、内容全部满足
            self.assertEqual(match_rule(hit, rules), "app-error")

            no_content = os.path.join(tmp, "app_2.log")
            _write(no_content, "xxxxxxxxxxOK")
            self.assertEqual(match_rule(no_content, rules), "log")

            no_prefix = os.path.join(tmp, "web_1.log")
            _write(no_prefix, "xxxxxxERROR")
            self.assertEqual(match_rule(no_prefix, rules), "log")

            too_small = os.path.join(tmp, "app_3.log")
            _write(too_small, "ERROR")
            self.assertEqual(match_rule(too_small, rules), "log")

    def test_max_size(self):
        rules = [
            {"name": "small", "suffix": "log", "max_size": 3},
            {"name": "log", "suffix": "log"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            small = os.path.join(tmp, "a.log")
            _write(small, "abc")
            self.assertEqual(match_rule(small, rules), "small")
            big = os.path.join(tmp, "b.log")
            _write(big, "abcdef")
            self.assertEqual(match_rule(big, rules), "log")

    def test_rule_priority(self):
        # error-log 写在后面，但 priority 更高，应先命中
        rules = [
            {"name": "log", "suffix": "log"},
            {"name": "error-log", "suffix": "log", "contains": "ERROR",
             "priority": 10},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.log")
            _write(src, "boom ERROR")
            self.assertEqual(match_rule(src, rules), "error-log")

    def test_load_rules_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "rules.json")
            with open(cfg, "w") as f:
                json.dump({"rules": [
                    {"name": "log", "suffix": "log"},
                    {"name": "error-log", "suffix": "log",
                     "contains": "ERROR", "priority": 5},
                ]}, f)
            rules = load_rules(cfg)
            self.assertEqual([r["name"] for r in rules], ["error-log", "log"])


class RouteTest(unittest.TestCase):
    def test_target_has_date_source_and_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.log")
            _write(src, "x")
            out = route(src, os.path.join(tmp, "out"),
                        source="sensor-1", date="2026-11-13")
            self.assertEqual(
                out,
                os.path.join(tmp, "out", "2026-11-13", "sensor-1", "log", "a.log"))

    def test_duplicate_name_gets_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = os.path.join(tmp, "inbox")
            os.makedirs(inbox)
            out_root = os.path.join(tmp, "out")

            first = os.path.join(inbox, "a.log")
            _write(first, "first")
            out1 = route(first, out_root, date="2026-11-13")

            second = os.path.join(inbox, "a.log")
            _write(second, "second")
            out2 = route(second, out_root, date="2026-11-13")

            self.assertTrue(out1.endswith(os.path.join("log", "a.log")))
            self.assertTrue(out2.endswith(os.path.join("log", "a-1.log")))
            with open(out1) as f:
                self.assertEqual(f.read(), "first")
            with open(out2) as f:
                self.assertEqual(f.read(), "second")

    def test_failure_moves_to_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "data.bin")
            _write(src, "\x00\x01")
            out_root = os.path.join(tmp, "out")
            out = route(src, out_root)

            pending = os.path.join(out_root, "_pending")
            self.assertEqual(os.path.dirname(out), pending)
            self.assertFalse(os.path.exists(src))
            info = read_reason(out)
            self.assertIn("no matching rule", info["reason"])
            self.assertEqual(info["source"], os.path.basename(tmp))

    def test_retry_pending_only_processes_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_root = os.path.join(tmp, "out")
            src = os.path.join(tmp, "data.bin")
            _write(src, "\x00\x01")
            route(src, out_root)  # 无规则命中，进入待处理

            # 重跑仍无命中，文件留在待处理目录
            results = retry_pending(out_root)
            self.assertEqual(len(results), 1)
            self.assertEqual(os.path.dirname(results[0][1]),
                             os.path.join(out_root, "_pending"))

            # 补上规则后重跑，只处理待处理目录并成功分发
            rules = [{"name": "bin", "suffix": "bin"}]
            results = retry_pending(out_root, rules=rules)
            self.assertEqual(len(results), 1)
            self.assertIn(os.sep + "bin" + os.sep, results[0][1])
            self.assertEqual(os.listdir(os.path.join(out_root, "_pending")), [])
