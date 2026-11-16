import unittest

from versionid import identify, override, register, report
from versionid import banner

PORT_80 = "HTTP/1.1 200 OK\r\nServer: nginx/1.18.0\r\n\r\n"
PORT_8080 = "HTTP/1.1 200 OK\r\nServer: nginx/1.22.1\r\n\r\n"
NO_HEADER = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<title>router</title>"
STRIPPED_HEADER = (
    "HTTP/1.1 200 OK\r\nServer: web\r\nContent-Type: text/html\r\n\r\n"
    "<html><title>Welcome to nginx!</title></html>"
)
SSH_BANNER = "SSH-2.0-OpenSSH_7.4\r\n"


class BannerTest(unittest.TestCase):
    def setUp(self):
        banner._RESPONSES.clear()
        banner._OVERRIDES.clear()

    def test_version_from_server_header(self):
        register("10.0.0.8", 80, PORT_80)
        item = identify("10.0.0.8", 80)
        self.assertEqual(item["version"], "1.18.0")
        self.assertEqual(item["confidence"], "high")

    def test_raw_response_is_kept(self):
        register("10.0.0.8", 80, PORT_80)
        self.assertIn("nginx/1.18.0", identify("10.0.0.8", 80)["raw"])

    def test_missing_header_is_unknown_and_keeps_raw(self):
        register("10.0.0.8", 81, NO_HEADER)
        item = identify("10.0.0.8", 81)
        self.assertEqual(item["service"], "unknown")
        self.assertTrue(item["unknown"])
        self.assertIn("<title>router</title>", item["raw"])

    def test_stripped_header_falls_back_to_default_page(self):
        register("10.0.0.8", 80, STRIPPED_HEADER)
        item = identify("10.0.0.8", 80)
        self.assertEqual(item["service"], "nginx")
        self.assertEqual(item["confidence"], "medium")
        # 改过的头也留在依据里，供人工核对
        self.assertTrue(any(e["signal"] == "server" for e in item["evidence"]))

    def test_protocol_banner(self):
        register("10.0.0.8", 22, SSH_BANNER)
        item = identify("10.0.0.8", 22)
        self.assertEqual(item["service"], "openssh")
        self.assertEqual(item["version"], "7.4")

    def test_report_lists_each_port_separately(self):
        register("10.0.0.8", 80, PORT_80)
        register("10.0.0.8", 8080, PORT_8080)
        result = report()
        self.assertEqual(result["ports"], 2)
        self.assertEqual(
            result["hosts"]["10.0.0.8"],
            [
                {"port": 80, "service": "nginx", "version": "1.18.0", "confidence": "high"},
                {"port": 8080, "service": "nginx", "version": "1.22.1", "confidence": "high"},
            ],
        )
        self.assertEqual(result["distribution"], {"nginx": {"1.18.0": 1, "1.22.1": 1}})

    def test_unknown_grouped_by_host(self):
        register("10.0.0.9", 81, NO_HEADER)
        register("10.0.0.9", 82, NO_HEADER)
        register("10.0.0.8", 80, PORT_80)
        self.assertEqual(report()["unknown"], {"10.0.0.9": [81, 82]})

    def test_manual_override_wins(self):
        register("10.0.0.9", 81, NO_HEADER)
        override("10.0.0.9", 81, "openwrt", "22.03", note="人工确认是路由器")
        item = identify("10.0.0.9", 81)
        self.assertEqual(item["service"], "openwrt")
        self.assertEqual(item["version"], "22.03")
        self.assertEqual(item["confidence"], "manual")
        self.assertFalse(item["unknown"])
        self.assertEqual(report()["unknown"], {})


if __name__ == "__main__":
    unittest.main()
