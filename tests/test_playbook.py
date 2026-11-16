import unittest

from irplaybook import open, reset, timeline


class PlaybookTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_open_returns_step_count(self):
        out = open({"id": "e1", "type": "ransomware"})
        self.assertEqual(out["steps"], 4)
        self.assertEqual(out["done"], 0)

    def test_steps_can_be_marked_done_out_of_order(self):
        out = open({"id": "e2", "type": "ransomware", "done": ["cleanup", "restore"]})
        self.assertEqual(out["done"], 2)
        steps = timeline("e2")["steps"]
        self.assertEqual([s["name"] for s in steps], ["isolate", "collect", "cleanup", "restore"])

    def test_records_are_empty(self):
        open({"id": "e3", "type": "phishing", "operator": "li", "done": ["quarantine"]})
        out = timeline("e3")
        self.assertEqual(out["records"], [])
        self.assertEqual(out["leftover"], [])

    def test_closable_with_pending_steps(self):
        open({"id": "e4", "type": "intrusion"})
        self.assertTrue(timeline("e4")["closable"])

    def test_bad_type_raises(self):
        with self.assertRaises(ValueError):
            open({"id": "e5", "type": "ddos"})


if __name__ == "__main__":
    unittest.main()
