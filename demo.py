from configdrift import baseline, diff, report

baseline({"timeout": "30", "workers": "8"})
print(diff({"host": "web1", "config": {"timeout": "31", "workers": "8"}}))
print(diff({"host": "web2", "config": {"timeout": "30", "workers": "8"}}))
print(report()["items"])

# TODO: 第二段演示（实现后补）
