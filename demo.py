from containerexit import check, report

print(check({"name": "web", "host": "h1", "privileged": True}))
print(check({"name": "db", "host": "h1", "privileged": False, "mounts": ["/var/run/docker.sock"]}))
print(report())

# TODO: 第二段演示（实现后补）
