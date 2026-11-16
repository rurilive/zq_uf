from reachable import reachable, report

deps = {
    "project": "shop",
    "modules": [
        {"name": "log4j", "version": "2.14.1"},
        {"name": "commons-io", "version": "1.2"},
    ],
    "vulnerable": [
        {"name": "log4j", "version": "2.14.1"},
        {"name": "commons-io", "version": "1.2.0"},
    ],
}

result = reachable(deps)
print(result)
print(result["reachable"])
print(report("shop")["count"])

# TODO: 第二段演示（实现后补）
