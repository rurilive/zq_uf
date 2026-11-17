from configdrift import baseline, diff, report, set_tolerance

baseline({"timeout": "30", "workers": "8"})
print(diff({"host": "web1", "config": {"timeout": "31", "workers": "8"}}))
print(diff({"host": "web2", "config": {"timeout": "30", "workers": "8"}}))
print(report()["items"])

# 第二段：允许差异范围、整改复核、重复漂移统计
baseline({"timeout": "30", "workers": "8"}, tolerances={"timeout": 2})
print(diff({"host": "web1", "config": {"timeout": "31", "workers": "8"}}))  # 范围内，通过
print(diff({"host": "web1", "config": {"timeout": "40", "workers": "8"}}))  # 漂移
print(diff({"host": "web1", "config": {"timeout": "30", "workers": "8"}}))  # 整改，通过
print(diff({"host": "web1", "config": {"timeout": "40", "workers": "8"}}))  # 再次漂移，repeats=2
print(report("timeout"))

# 管理员调整允许范围，需要二次确认
pending = set_tolerance("timeout", 10)
print(set_tolerance("timeout", 10, confirm=pending["token"]))
