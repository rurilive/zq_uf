from redteam import (acquire, complete, fail, halt, plan, progress, recover,
                     rollback, start, timeline)

script = {
    "name": "演练甲",
    "owner": "张队",
    "stages": [
        {"name": "侦察", "goal": "收集资产", "owner": "李工", "after": []},
        {"name": "打点", "goal": "拿到入口", "owner": "王工", "after": ["侦察"]},
        {"name": "横向", "goal": "扩展权限", "owner": "王工", "after": ["打点"]},
    ],
}

# 第一段：阶段编排与依赖。打点的前置侦察没完成，直接开始会被挡住。
print("阶段:", [(s["name"], s["status"], s["owner"]) for s in plan(script)["stages"]])
print("跳着开始:", start("演练甲", "打点"))
print("开始侦察:", start("演练甲", "侦察", at="2026-11-17T09:00:00+08:00"))
print("拿到权限:", acquire("演练甲", "侦察", "域账号读权限",
                          at="2026-11-17T09:05:00+08:00")["asset"])
print("侦察完成:", complete("演练甲", "侦察", result="拿到 12 台资产",
                            at="2026-11-17T09:20:00+08:00")["status"])
print("打点开始:", start("演练甲", "打点", at="2026-11-17T09:21:00+08:00")["status"])

# 第二段：阶段失败留证，后续阶段不自动开始；回退要写原因。
print("打点失败:", fail("演练甲", "打点", "入口被封，需换路径",
                        at="2026-11-17T09:40:00+08:00")["reason"])
print("横向仍待执行:",
      [s["status"] for s in timeline("演练甲")["stages"]])
print("回退打点:", rollback("演练甲", "打点", "改从 VPN 入口重打",
                            at="2026-11-17T09:45:00+08:00")["status"])

# 第三段：叫停、逐项回收，全部回收后演练才结束。
start("演练甲", "打点", at="2026-11-17T09:50:00+08:00")
acquire("演练甲", "打点", "VPN 账号", at="2026-11-17T09:55:00+08:00")
stopped = halt("演练甲", "业务方叫停", at="2026-11-17T10:00:00+08:00")
print("叫停后待回收:", [a["name"] for a in stopped["assets"]])

print("回收域账号:", recover("演练甲", "域账号读权限", by="李工",
                             at="2026-11-17T10:30:00+08:00")["status"])
still = recover("演练甲", "VPN 账号", ok=False,
                note="VPN 会话未断开，供应商侧处理中", by="王工",
                at="2026-11-17T10:30:00+08:00")
print("没回收成功:", [(a["name"], a["note"]) for a in still["unrecovered"]])
print("演练状态:", still["status"])

done = recover("演练甲", "VPN 账号", by="王工", at="2026-11-17T11:00:00+08:00")
print("全部回收后:", done["status"])

# 复盘素材直接从时间线取；负责人按演练看进度。
print("时间线:")
for entry in timeline("演练甲")["entries"]:
    print(" ", entry["seq"], entry["at"], entry["type"], entry["stage"], entry["detail"])
print("按执行人进度:", progress("演练甲")["by_owner"])
