from reachable import reachable, report

deps = {
    "project": "shop",
    "modules": [
        {"name": "log4j", "version": "2.14.1"},
        {"name": "commons-io", "version": "1.2"},
        {"name": "groovy", "version": "3.0.9"},
    ],
    "vulnerable": [
        {"name": "log4j", "version": "2.14.1"},
        {"name": "commons-io", "version": "1.2.0"},  # 写法不同，归一后命中
        {"name": "groovy", "version": "3.0.9"},
    ],
    "entries": [{"name": "shop.web.main", "source": "http"}],
    "calls": [
        {"from": "shop.web.main", "to": "shop.web.handler"},
        {"from": "shop.web.handler", "to": "log4j"},
    ],
    "dynamic": [{"component": "groovy", "via": "reflection"}],
}

result = reachable(deps)
print(result["reachable"][0]["paths"])   # 入口 → log4j 的调用路径
print(result["unreachable"])             # commons-io：未被任何代码调用
print(result["pending"])                 # groovy：反射调用，待确认
print(report("shop")["count"])
