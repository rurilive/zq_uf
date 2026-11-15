"""分区迁移演示：双写 / 追平 / 校验 / 原子切清单 / 回滚。"""

from partmove2 import Migration

engine = Migration(retention_seconds=600)

engine.write_many("orders-11", {"o%d" % i: {"amount": i * 10} for i in range(1, 6)})
engine.write_many("orders-12", {"o%d" % i: {"amount": i * 10} for i in range(1, 4)})
print("初始:", engine.progress("orders-11")["source_rows"], "行在 orders-11")

print("\n=== 1. 低流量分区演练 ===")
rehearsal = engine.rehearse("orders-12", "cold/orders-12")
print("阶段:", rehearsal["stage"], "| 位点:", rehearsal["position"])
print("演练检查:", rehearsal["checks"])

print("\n=== 2. 双写 + 位点 ===")
engine.start("orders-11", "cold/orders-11")
engine.write("orders-11", "o6", {"amount": 60})   # 迁移中写入，两条路径同步
engine.catch_up("orders-11", limit=2)             # 分批回填，可观察进度
mid = engine.progress("orders-11")
print("阶段:", mid["stage"], "| 位点:", mid["position"], "| lag:", mid["lag"])
print("两侧行数: 旧", mid["source_rows"], "新", mid["target_rows"])
print("迁移期间读:", sorted(engine.read("orders-11")))  # 全程可读，不会是半份

print("\n=== 3. 追平前拒绝切换 ===")
early = engine.cutover("orders-11")
print("切换结果:", early["switched"], "|", early["error"])

print("\n=== 4. 追平 + 校验 + 切换 ===")
engine.catch_up("orders-11")
check = engine.verify("orders-11")
print("校验: 旧 %d 行 / 新 %d 行 | 内容一致: %s"
      % (check["source_rows"], check["target_rows"], check["content_match"]))
switched = engine.cutover("orders-11")
report = engine.progress("orders-11")
print("切换结果:", switched["switched"], "| 清单版本:", switched["version"],
      "| 当前路径:", report["current_path"])
print("返回: 阶段 %s / 位点 %s / 回滚清单 %s"
      % (report["stage"], report["position"],
         [(e["partition"], e["target"]) for e in report["rollback_manifest"]]))

print("\n=== 5. 切换失败：停在旧路径 + 回滚保留目标 ===")
engine2 = Migration(retention_seconds=600)
engine2.write_many("orders-13", {"x": 1, "y": 2})
engine2.start("orders-13", "cold/orders-13")
engine2.catch_up("orders-13")
engine2.fail_switch = True
failed = engine2.cutover("orders-13")
print("切换结果:", failed["switched"], "| 阶段:", failed["phase"],
      "|", failed["error"])
print("仍在旧路径:", engine2.progress("orders-13")["current_path"])
rolled = engine2.rollback("orders-13")
print("回滚清单:", rolled["rollback_manifest"])
print("目标分区保留到:", rolled["retained"]["orders-13"]["until"])

print("\n=== 6. 回滚 orders-11 ===")
rolled11 = engine.rollback("orders-11")
print("回滚:", rolled11["rolled_back"], "| restored:", rolled11["restored"])
print("保留:", rolled11["retained"])
print("回滚后读写仍正常:", engine.read("orders-11")["o6"])
print("目标分区仍在:", engine.get_store().exists("cold/orders-11"))
