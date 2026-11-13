import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

EXPECTED = "\n".join([
    "== 默认索引 ==",
    "orders -> ['dwd_orders']",
    "订单 -> ['dwd_orders', 'dws_sales']",
    "amount -> ['dwd_orders']",
    "== 自建索引 ==",
    "订单 -> ['dwd_order_detail', 'dws_sales_daily']",
    "  dwd_order_detail score=3.777144 hit=name:dwd_order_detail",
    "  dws_sales_daily score=2.156452 hit=field:order_id",
    "amo -> ['dwd_order_detail', 'ods_payment']",
    "  dwd_order_detail score=1.777144 hit=field:amount",
    "  ods_payment score=1.651086 hit=field:amount",
    "成交额 -> ['dws_sales_daily']",
    "  dws_sales_daily score=2.156452 hit=field:gmv",
    "序列化复现 -> True",
    "",
])


class DemoTest(unittest.TestCase):
    def test_demo_output_matches(self):
        result = subprocess.run(
            [sys.executable, "demo.py"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout, EXPECTED)


if __name__ == "__main__":
    unittest.main()
