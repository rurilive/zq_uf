import unittest
from datetime import datetime, timedelta, timezone

from webshell import report, scan, whitelist

FILES = {
    "upload/avatar.php": "<?php\neval($_POST['c']);\n",
    "upload/util.php": "<?php\nreturn 1;\n",
}


class ScanTest(unittest.TestCase):
    def test_keyword_hit_keeps_file_name(self):
        result = scan(FILES)
        self.assertEqual(result["hits"], 1)
        self.assertEqual(result["files"], ["upload/avatar.php"])

    def test_clean_file_is_not_reported(self):
        self.assertEqual(scan({"upload/util.php": "return 1;\n"})["hits"], 0)

    def test_eval_word_in_comment_and_variable_is_false_positive(self):
        text = (
            "<?php\n"
            "// eval() is forbidden by the review rule\n"
            "$evaluation = $_GET['name'];\n"
            "echo htmlspecialchars($evaluation);\n"
        )
        result = scan({"upload/biz.php": text})
        self.assertEqual(result["hits"], 0)
        self.assertEqual(result["files"], [])

    def test_obfuscated_concat_is_restored_and_detected(self):
        text = "<?php\n$f = 'ev'.'al';\n$f($_POST['c']);\n"
        result = scan({"upload/loader.php": text})
        self.assertEqual(result["files"], ["upload/loader.php"])
        finding = result["findings"][0]
        techniques = {record["technique"] for record in finding["restoration"]}
        self.assertIn("string_concat", techniques)
        targets = {match["target"] for match in finding["matches"]}
        self.assertIn("eval", targets)

    def test_base64_wrapped_eval_is_decoded_and_detected(self):
        # eval($_POST['c']); 的 base64
        text = "<?php\n$p='ZXZhbCgkX1BPU1RbJ2MnXSk7';\neval(base64_decode($p));\n"
        result = scan({"upload/b64.php": text})
        self.assertEqual(result["files"], ["upload/b64.php"])
        finding = result["findings"][0]
        self.assertTrue(
            any("eval" in record["to"] for record in finding["restoration"])
        )

    def test_repeat_hits_in_one_file_are_merged_with_count(self):
        text = "eval($_GET['a']);\neval($_GET['b']);\neval($_GET['c']);\n"
        result = scan({"upload/two.php": text})
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["count"], 3)
        self.assertEqual(result["hits"], 3)

    def test_hit_keeps_line_number_and_one_line_context(self):
        text = "<?php\n$x = 1;\neval($_POST['c']);\nreturn $x;\n"
        finding = scan({"upload/avatar.php": text})["findings"][0]
        match = next(m for m in finding["matches"] if m["rule"] == "sink_with_user_input")
        self.assertEqual(match["line"], 3)
        snippet = match["snippet"]
        self.assertEqual([item["line"] for item in snippet], [2, 3, 4])
        self.assertEqual(snippet[0]["text"], "$x = 1;")
        self.assertIn("eval", snippet[1]["text"])
        self.assertEqual(snippet[2]["text"], "return $x;")

    def test_similar_files_in_same_directory_are_grouped(self):
        files = {
            "upload/a1.php": "<?php\n$a = 'ev'.'al';\n$a($_REQUEST['x']);\n",
            "upload/a2.php": "<?php\n$b = 'ev'.'al';\n$b($_REQUEST['y']);\n",
            "upload/clean.php": "<?php\nreturn 1;\n",
        }
        result = scan(files)
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(
            sorted(result["groups"][0]["files"]),
            ["upload/a1.php", "upload/a2.php"],
        )
        group_id = result["groups"][0]["id"]
        for finding in result["findings"]:
            if finding["file"] in ("upload/a1.php", "upload/a2.php"):
                self.assertEqual(finding["group_id"], group_id)

    def test_whitelist_only_affects_named_file(self):
        whitelist("upload/avatar.php")
        try:
            result = scan(
                {
                    "upload/avatar.php": FILES["upload/avatar.php"],
                    "upload/other.php": "<?php\neval($_POST['z']);\n",
                }
            )
            self.assertEqual(result["files"], ["upload/other.php"])
            self.assertIn("upload/avatar.php", result["whitelisted"])
        finally:
            from webshell.scan import _whitelist

            _whitelist.discard("upload/avatar.php")

    def test_behavior_signals_raise_score_but_never_report_alone(self):
        created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        access = [{"method": "POST", "post": "cmd=whoami", "status": 200}]
        clean = {
            "upload/clean.php": {
                "content": "<?php\nreturn 1;\n",
                "created_at": created,
                "access": access,
            }
        }
        self.assertEqual(scan(clean)["hits"], 0)

        plain = scan({"upload/shell.php": "<?php\nsystem($_POST['cmd']);\n"})
        enriched = scan(
            {
                "upload/shell.php": {
                    "content": "<?php\nsystem($_POST['cmd']);\n",
                    "created_at": created,
                    "access": access,
                }
            }
        )
        self.assertGreater(
            enriched["findings"][0]["score"], plain["findings"][0]["score"]
        )
        signal_types = {s["type"] for s in enriched["findings"][0]["behavior"]}
        self.assertIn("recently_created", signal_types)
        self.assertIn("suspicious_request", signal_types)

    def test_report_is_organized_by_directory(self):
        scan(FILES)
        body = report()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["counts"], {"upload/avatar.php": 1})
        self.assertIn("upload", body["by_directory"])
        entry = body["by_directory"]["upload"]["files"][0]
        self.assertEqual(entry["file"], "avatar.php")
        self.assertIn("risk", entry)
        self.assertIn("first_line", entry)

    def test_validation_and_api_surface_unchanged(self):
        with self.assertRaises(ValueError):
            scan(["not", "a", "dict"])
        with self.assertRaises(ValueError):
            whitelist("")
        result = whitelist("upload/temp.php")
        self.assertEqual(result["path"], "upload/temp.php")
        from webshell.scan import _whitelist

        _whitelist.discard("upload/temp.php")


if __name__ == "__main__":
    unittest.main()
