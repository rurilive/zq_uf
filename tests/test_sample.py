import unittest

from logsample import lines, reset, write


class SampleTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_writes_every_record(self):
        for index in range(20):
            write({"url": f"/{index}", "status": 200})
        self.assertEqual(len(lines()), 20)

    def test_errors_not_special(self):
        write({"url": "/1", "status": 500})
        write({"url": "/2", "status": 200})
        self.assertEqual(len(lines()), 2)

    def test_no_sampling_fields(self):
        row = write({"url": "/1", "status": 200})
        self.assertNotIn("sampled", row)
