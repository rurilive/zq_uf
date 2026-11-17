from patchimpact import assess, diff, export, report, reverse_lookup

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

# 第二段：版本范围排除、按环境导出、多次评估对比、组件反查
patch2 = {
    "name": "libxml2",
    "version": "2.9.2",
    "environment": "prod",
    "components": patch["components"] + [
        {"name": "legacy", "version": "1.0", "depends_on": ["libxml2<2.0"], "environment": "prod"},
        {"name": "scanner", "version": "0.9", "depends_on": ["exporter"], "environment": "prod"},
    ],
}
result = assess(patch2)
print(result["excluded"])            # legacy 版本不在范围内，带理由
print(result["deep_components"])     # 层级 >= 3 的组件单独标注
print(export(result, "prod"))        # 按环境分别导出
print(diff(assess(patch), result))   # 同一补丁两次评估的差异
print(reverse_lookup("exporter"))    # 从组件名反查受哪些补丁影响
