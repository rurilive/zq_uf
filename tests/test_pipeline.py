import unittest

from logparse import FORMATS, LogFormat, parse, parse_result, register_format


class QuotedFieldsTest(unittest.TestCase):
    def test_quoted_value_keeps_spaces(self):
        rows = parse(['[2026-03-01 10:00:00 +0800] ERROR user="Jane Doe" msg="disk full"'])
        self.assertEqual(rows[0]["fields"]["user"], "Jane Doe")
        self.assertEqual(rows[0]["fields"]["msg"], "disk full")

    def test_escaped_quote_and_backslash(self):
        rows = parse(['[2026-03-01 10:00:00 +0800] ERROR msg="say \\"hi\\" path C:\\\\tmp"'])
        self.assertEqual(rows[0]["fields"]["msg"], 'say "hi" path C:\\tmp')

    def test_plain_message_with_quoted_segment(self):
        rows = parse(['2026-03-01 INFO service "api gateway" started'])
        self.assertEqual(rows[0]["text"], "service api gateway started")


class StackMergeTest(unittest.TestCase):
    def test_stack_lines_merge_into_previous(self):
        rows = parse([
            "2026-03-01T10:00:00+08:00 ERROR save failed",
            "Traceback (most recent call last):",
            '  File "app.py", line 10, in <module>',
            "ValueError: boom",
            "2026-03-01T10:00:01+08:00 INFO recovered",
        ])
        self.assertEqual(len(rows), 2)
        self.assertIn("Traceback", rows[0]["text"])
        self.assertIn('File "app.py"', rows[0]["text"])
        self.assertIn("ValueError: boom", rows[0]["text"])
        self.assertEqual(rows[1]["text"], "recovered")


class TimezoneTest(unittest.TestCase):
    def test_offset_converted_to_utc(self):
        rows = parse(["2026-03-01T10:00:00+08:00 INFO started"])
        self.assertEqual(rows[0]["time"], "2026-03-01T02:00:00+00:00")

    def test_bracket_offset_converted_to_utc(self):
        rows = parse(["[2026-03-01 10:00:00 +0800] WARN low disk"])
        self.assertEqual(rows[0]["time"], "2026-03-01T02:00:00+00:00")

    def test_naive_treated_as_utc(self):
        rows = parse(["2026-03-01 INFO started"])
        self.assertEqual(rows[0]["time"], "2026-03-01T00:00:00+00:00")


class FailureKeepTest(unittest.TestCase):
    def test_failure_kept_with_raw_and_reason(self):
        result = parse_result(["2026-03-01 INFO ok", "garbage line here"])
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.failures[0]["raw"], "garbage line here")
        self.assertTrue(result.failures[0]["reason"])

    def test_recognized_but_bad_line_kept(self):
        result = parse_result(["2026-03-01"])
        self.assertEqual(result.records, [])
        self.assertEqual(result.stats["failed"], 1)
        self.assertIn("plain", result.failures[0]["reason"])

    def test_stats_split_by_format_and_failure(self):
        result = parse_result([
            "2026-03-01 INFO ok",
            '[2026-03-01 10:00:00 +0800] WARN msg="hi there"',
            "garbage",
        ])
        self.assertEqual(result.stats["formats"], {"plain": 1, "bracket": 1})
        self.assertEqual(result.stats["failed"], 1)
        self.assertEqual(result.stats["parsed"], 2)


class CustomFormatTest(unittest.TestCase):
    def test_register_format(self):
        class PipeFormat(LogFormat):
            name = "pipe"

            def match(self, line):
                return line.startswith("|")

            def parse(self, line):
                _, level, text = line.split("|", 2)
                return {"time": None, "level": level, "text": text.strip(),
                        "format": self.name}

        fmt = register_format(PipeFormat())
        try:
            rows = parse(["|INFO|hello pipe"])
            self.assertEqual(rows[0]["format"], "pipe")
            self.assertEqual(rows[0]["text"], "hello pipe")
        finally:
            FORMATS.remove(fmt)


if __name__ == "__main__":
    unittest.main()
