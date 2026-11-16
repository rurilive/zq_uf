from patchimpact import assess, diff, find_by_component, report

patch = {
    "name": "libxml2",
    "version": "2.9.1",
    "environment": "prod",
    "version_ranges": {"report": ">=1.0,<2.0"},
    "components": [
        {"name": "portal", "version": "3.0", "depends_on": ["libxml2"], "environment": "prod"},
        {"name": "report", "version": "1.4", "depends_on": ["libxml2"], "environment": "test"},
        {"name": "exporter", "version": "2.1", "depends_on": ["report"], "environment": "prod"},
    ],
}

result = assess(patch)
print(result["components"])
print(result["environments"])
print(report("libxml2")["count"])

# 版本范围排除、按环境导出、多次评估对比、组件反查
patch_out_of_range = dict(patch, version_ranges={"report": ">=2.0"})
second = assess(patch_out_of_range)
print(second["excluded"])
print(report("libxml2", environment="prod")["assessments"][0]["components"])
print(diff(result, second))
print(find_by_component("exporter"))
