from wmsync import (
    heartbeat,
    late_events,
    reset,
    restore,
    snapshot,
    status,
    update,
    watermark,
)

update("source-a", 100)
update("source-b", 40)
print(watermark())

# 第二段：source-b 停更，心跳推进空闲源，迟到进侧输出，快照可恢复
reset()
update("source-a", 10)
update("source-b", 12)
update("source-a", 30)          # b 停更，全局水位卡在 12
print(status())
heartbeat(45, idle_timeout=20)  # b 空闲，水位推进到 25
print(status())
update("source-a", 20)          # 20 < 25，迟到，进侧输出
print(late_events())
update("source-b", 40)          # b 恢复，重新参与最小水位
print(status())
snap = snapshot()               # 可 json.dumps 序列化
reset()
restore(snap)                   # 重启恢复
print(watermark())
