from configdrift import baseline, diff, history, report, set_range

baseline({"timeout": "30", "workers": "8"})
print(diff({"host": "web1", "config": {"timeout": "31", "workers": "8"}}))
print(diff({"host": "web2", "config": {"timeout": "30", "workers": "8"}}))
print(report()["items"])

# 第二段：允许差异范围、整改复测、重复漂移统计
baseline({"timeout": "30", "workers": "8"}, ranges={"timeout": {"tolerance": 5}})
print(diff({"host": "web3", "config": {"timeout": "33", "workers": "8"}}))  # 范围内，通过
print(set_range("workers", ["8", "16"]))  # 待二次确认
print(set_range("workers", ["8", "16"], confirm=True))  # 确认后生效
print(diff({"host": "web3", "config": {"timeout": "60", "workers": "8"}}))  # 漂移
print(diff({"host": "web3", "config": {"timeout": "30", "workers": "8"}}))  # 整改后复测通过
print(diff({"host": "web3", "config": {"timeout": "61", "workers": "8"}}))  # 再次漂移，repeats=2
print(report("timeout", host="web3"))
print(history(key="timeout", host="web3"))  # 按时间回看
