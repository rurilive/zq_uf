from cdcordering import apply, conflicts, reset, snapshot

apply([{"key": "a", "row": {"v": 2}}, {"key": "a", "row": {"v": 1}}])
print(snapshot())

# 第二段：乱序投递一组带版本的事件，收敛到最高版本
reset()
events = [
    {"key": "a", "row": {"v": 2}, "version": {"s1": 2}, "source": "s1"},
    {"key": "a", "row": {"v": 1}, "version": {"s1": 1}, "source": "s1"},  # 旧版本乱序到达
    {"key": "b", "row": {"v": 1, "deleted": True}, "version": {"s1": 3}, "source": "s1"},
    {"key": "b", "row": {"v": 9}, "version": {"s1": 1}, "source": "s1"},  # 旧更新，不能复活墓碑
    {"key": "c", "row": {"v": 1}, "version": {"s1": 1}, "source": "s1"},
    {"key": "c", "row": {"v": 2}, "version": {"s2": 1}, "source": "s2"},  # 并发冲突
    {"key": "a", "row": {"v": 2}, "version": {"s1": 2}, "source": "s1"},  # 重复投递
]
preview = apply(events, dry_run=True)
print("dry-run 将变化的行:", [c["key"] for c in preview["changes"]])
result = apply(events)
print("snapshot:", snapshot())
print("discarded:", [(d["event"]["key"], d["reason"]) for d in result["discarded"]])
print("conflicts:", conflicts())
