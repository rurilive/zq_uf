import unittest

from versionid import identify, register, report
from versionid import banner

PORT_80 = "HTTP/1.1 200 OK\r\nServer: nginx/1.18.0\r\n\r\n"
PORT_8080 = "HTTP/1.1 200 OK\r\nServer: nginx/1.22.1\r\n\r\n"
NO_HEADER = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<title>router</title>"


class BannerTest(unittest.TestCase):
    def setUp(self):
        banner._RESPONSES.clear()

    def test_version_from_server_header(self):
        register("10.0.0.8", 80, PORT_80)
        self.assertEqual(identify("10.0.0.8", 80)["version"], "1.18.0")

    def test_raw_response_is_not_kept(self):
        register("10.0.0.8", 80, PORT_80)
        self.assertEqual(identify("10.0.0.8", 80)["raw"], "")

    def test_missing_header_is_unknown(self):
        register("10.0.0.8", 81, NO_HEADER)
        self.assertEqual(identify("10.0.0.8", 81)["service"], "unknown")

    def test_report_keeps_one_version_per_host(self):
        register("10.0.0.8", 80, PORT_80)
        register("10.0.0.8", 8080, PORT_8080)
        result = report()
        self.assertEqual(result["ports"], 2)
        self.assertEqual(result["hosts"], {"10.0.0.8": {"service": "nginx", "version": "1.22.1"}})

    def test_unknown_list_is_always_empty(self):
        register("10.0.0.8", 81, NO_HEADER)
        self.assertEqual(report()["unknown"], [])
