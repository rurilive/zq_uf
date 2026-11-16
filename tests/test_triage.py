import unittest

from crashdedupe import report, triage

CRASHES = [
    {
        "id": "s1",
        "target": "pdf",
        "stack": ["parse_obj+0x12", "main+0x40"],
        "signal": "SIGSEGV",
        "fault_addr": "0x0",
    },
    {
        "id": "s2",
        "target": "pdf",
        "stack": ["parse_obj+0x31", "main+0x40"],
        "signal": "SIGABRT",
        "fault_addr": "0x18",
    },
    {
        "id": "s3",
        "target": "pdf",
        "stack": ["read_len+0x04", "main+0x40"],
        "signal": "SIGSEGV",
        "fault_addr": "0x0",
    },
]


class TriageTest(unittest.TestCase):
    def test_same_top_frame_is_merged(self):
        issues = triage(CRASHES)["issues"]
        self.assertEqual(issues[0]["count"], 2)
        self.assertEqual(issues[0]["samples"], ["s1", "s2"])

    def test_different_top_frame_is_separate(self):
        self.assertEqual(len(triage(CRASHES)["issues"]), 2)

    def test_reason_is_stack_top(self):
        issues = triage(CRASHES)["issues"]
        self.assertEqual(issues[0]["reason"], "stack_top")

    def test_report_counts_samples_per_target(self):
        triage(CRASHES)
        body = report()
        self.assertEqual(body["by_target"], {"pdf": 3})
        self.assertEqual(body["issues"], 2)
