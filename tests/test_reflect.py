import unittest

from xssreflect import PAYLOAD, check, load, report


class ReflectTest(unittest.TestCase):
    def test_raw_echo_counted_once(self):
        load("search", "<div>%s</div>" % PAYLOAD)
        out = check("search", "q")
        self.assertEqual(len(out["findings"]), 1)
        self.assertEqual(out["findings"][0]["context"], "unknown")

    def test_escaped_echo_missed(self):
        load("safe", "<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>")
        out = check("safe", "q")
        self.assertEqual(out["findings"], [])

    def test_two_echoes_kept_as_two(self):
        load("double", "%s and %s" % (PAYLOAD, PAYLOAD))
        out = check("double", "q")
        self.assertEqual(len(out["findings"]), 2)
        self.assertEqual([item["offset"] for item in out["findings"]], [0, 30])

    def test_report_has_no_context_split(self):
        load("search", "%s" % PAYLOAD)
        check("search", "q")
        rep = report()
        self.assertEqual(rep["hits"], 1)
        self.assertEqual(rep["contexts"], {})
        self.assertEqual(rep["uncovered"], 0)

    def test_reject_unknown_page(self):
        with self.assertRaises(ValueError):
            check("missing", "q")


if __name__ == "__main__":
    unittest.main()
