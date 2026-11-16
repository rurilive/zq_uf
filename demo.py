from redteam import halt, plan, start, timeline

script = {
    "name": "演练甲",
    "stages": [
        {"name": "侦察", "goal": "收集资产", "after": []},
        {"name": "打点", "goal": "拿到入口", "after": ["侦察"]},
    ],
}

print(plan(script)["stages"])
print(start("演练甲", "打点"))
print(halt("演练甲", "业务方叫停"))
print(timeline("演练甲")["entries"])

# TODO: 第二段演示（实现后补）
