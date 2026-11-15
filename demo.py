from jobwindow import close, open

print(open({"name": "hourly", "upstream": ["extract"]}))
print(close({"name": "hourly"}))

# TODO: 第二段演示（实现后补）
