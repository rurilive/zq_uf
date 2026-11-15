"""proxysettle 演示：采集失败退费、出口切换退费、按天落盘对账。

运行：python3 -I demo.py
"""

import os
import sys
import tempfile

# 兼容 python3 -I demo.py：隔离模式不会把脚本所在目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import proxysettle

# 启用按天落盘；真实部署时换成固定目录，重启后账本自动恢复
data_dir = tempfile.mkdtemp(prefix="proxysettle-demo-")
proxysettle.init(data_dir)
print("账本数据目录:", data_dir)
print("-" * 60)

BATCH = "batch-20261115"

# 1) 第一批：成功入库 2 条，失败 1 条，超时 1 条
proxysettle.charge("proxy-a", 1.5, batch=BATCH)   # 成功且入库
proxysettle.charge("proxy-a", 2.0, batch=BATCH)   # 成功且入库
failed = proxysettle.charge("proxy-a", 1.0, success=False, batch=BATCH)
timeout = proxysettle.charge("proxy-a", 1.2, timeout=True, batch=BATCH)
print("失败流水: 退费 %.2f, 口径=%s" % (failed["refund"], failed["outcome"]))
print("超时流水: 退费 %.2f, 口径=%s" % (timeout["refund"], timeout["outcome"]))

# 2) 出口切换：proxy-a 在该批次未结算的计费全额退回，记录关联原出口和批次
switch = proxysettle.switch_proxy("proxy-a", "proxy-b", batch=BATCH,
                                  reason="exit-unreachable")
print("切换记录: 旧出口=%s -> 新出口=%s, 批次=%s, 退回旧出口费用 %.2f"
      % (switch["old_proxy"], switch["new_proxy"], switch["batch"],
         switch["refund_amount"]))

# 3) 新出口继续采集：成功入库 3 条，其中 1 条抓到但没入库（全额退费）
proxysettle.charge("proxy-b", 2.5, batch=BATCH)
proxysettle.charge("proxy-b", 2.5, batch=BATCH)
proxysettle.charge("proxy-b", 2.5, batch=BATCH)
proxysettle.charge("proxy-b", 2.5, stored=False, batch=BATCH)

# 4) 登记真实入库行数并按天结算对账
#    proxy-a 切换前确实入库 2 行；切换退费后计费条数为 0，diff=-2 如实提示
#    "入库行仍挂在旧出口名下"，月末按站点对账时据此把行数划归新出口。
#    proxy-b 实际只入库 2 行（1 条抓到未入库已退费），diff=1 提示少入一行。
proxysettle.mark_stored("proxy-a", 2)
proxysettle.mark_stored("proxy-b", 2)

print("-" * 60)
for proxy in ("proxy-a", "proxy-b"):
    s = proxysettle.settle(proxy)
    print("[%s] 计费 %.2f (%d 条) | 退费 %.2f (%d 条) | 余额 %.2f"
          % (proxy, s["charges"], s["charge_count"], s["refunds"],
             s["refund_count"], s["balance"]))
    print("       计费条数=%d 入库行数=%s 差异=%s 已对账=%s"
          % (s["billable_count"], s["stored_rows"], s["diff"],
             s["reconciled"]))
    if s["diff"]:
        print("       提示: 差异非 0，计费条数与入库行数不一致，需人工核对")

# 5) 模拟重启：用同一数据目录重新打开账本，结算单仍在
print("-" * 60)
reloaded = proxysettle.SettleBook(data_dir)
day = proxysettle.load_settlement()["date"]
loaded = reloaded.load_settlement(day)
print("重启后读取日结算单 %s：" % day)
for proxy, s in loaded["proxies"].items():
    print("  %s 余额=%.2f 退费=%.2f 差异=%s"
          % (proxy, s["balance"], s["refunds"], s["diff"]))
