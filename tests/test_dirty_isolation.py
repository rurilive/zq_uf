import gzip
import os
import tempfile
import unittest

from sitemaptool import load_sources

GOOD = """<?xml version="1.0"?>
<urlset><url><loc>http://shop.example/ok</loc></url></urlset>
"""

BROKEN = "<?xml version=\"1.0\"?><urlset><url><loc>http://shop.example/x</loc>"


class DirtyIsolationTest(unittest.TestCase):
    def test_bad_xml_and_truncated_gz_are_skipped_with_sample_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.xml")
            broken = os.path.join(tmp, "broken.xml")
            bad_gz = os.path.join(tmp, "truncated.xml.gz")
            with open(good, "w", encoding="utf-8") as handle:
                handle.write(GOOD)
            with open(broken, "w", encoding="utf-8") as handle:
                handle.write(BROKEN)
            buffer = gzip.compress(b"<urlset>" + b"<url/>" * 4000)
            with open(bad_gz, "wb") as handle:
                handle.write(buffer[: len(buffer) // 2])

            with self.assertLogs("sitemaptool", level="WARNING") as logs:
                entries = load_sources([good, broken, bad_gz])

        self.assertEqual([entry["loc"] for entry in entries], ["http://shop.example/ok"])
        joined = "\n".join(logs.output)
        self.assertIn("broken.xml", joined)
        self.assertIn("truncated.xml.gz", joined)
        self.assertIn("样本", joined)


if __name__ == "__main__":
    unittest.main()
