from patchimpact import assess, report

patch = {
    "name": "libxml2",
    "version": "2.9.1",
    "components": [
        {"name": "portal", "version": "3.0", "depends_on": ["libxml2"], "environment": "prod"},
        {"name": "report", "version": "1.4", "depends_on": ["libxml2"], "environment": "test"},
        {"name": "exporter", "version": "2.1", "depends_on": ["report"], "environment": "prod"},
    ],
}

print(assess(patch)["components"])
print(assess(patch)["environments"])
print(report("libxml2")["count"])

# TODO: 第二段演示（实现后补）
