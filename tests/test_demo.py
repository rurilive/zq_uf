import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        out = subprocess.run(
            [sys.executable, "demo.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        lines = out.strip().splitlines()
        fallback = "{'eta': 210.0, 'low': 210.0, 'high': 210.0, 'n': 4, 'outliers': 0}"
        self.assertEqual(lines[0], fallback)
        self.assertEqual(lines[1], fallback)
        self.assertIn("'eta': 659.089", lines[2])
        self.assertIn("'n': 5, 'outliers': 1", lines[2])
        self.assertIn("'eta': 220.47", lines[3])
        self.assertIn("{'task': 'etl', 'size': 5000, 'duration': 5000.0}", lines[4])
        self.assertEqual(lines[5], "backtest: count=3 mae=1482.77 coverage=0.67")


if __name__ == "__main__":
    unittest.main()
