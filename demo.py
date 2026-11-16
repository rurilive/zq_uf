"""WAF 规则回归演示。

场景：安全同学要把 XSS 规则从精确的 <script> 放宽为单个尖括号 <，
并给登录业务线补一条注入规则。演示完整闭环：
  第一段：改动前跑全量基线，查看拦截/误拦/漏拦两侧结果；
  第二段：执行前先看变更影响范围，只跑受影响用例，再和基线对比，
          误拦用例按业务场景分组并带原始请求，可直接复现；
  第三段：用文件留存引擎重放，历史结果落盘，重启后仍可查可对比。
"""
import os
import tempfile

from wafregress import Engine, configure, impact, load_cases, regress, report, compare
from wafregress.cases import load_case_files
from wafregress.store import FileStore

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.join(HERE, "examples", "cases")

# 改动前的规则：精确匹配，漏拦一条登录注入，业务侧零误拦
RULES_V1 = [
    {"id": "sqli", "pattern": "union select"},
    {"id": "xss", "pattern": "<script>"},
]
# 改动后的规则：xss 放宽为 <（容易误伤带尖括号的正常业务）；
# 新增只作用于 login 业务线的注入规则
RULES_V2 = [
    {"id": "sqli", "pattern": "union select"},
    {"id": "xss", "pattern": "<"},
    {"id": "login_sqli", "pattern": "'--", "scope": "login"},
]


def print_header(title):
    print(f"\n{'=' * 20} {title} {'=' * 20}")


def stage_one_baseline():
    print_header("第一段：改动前全量基线")
    summary = regress(RULES_V1)
    print("回归汇总（拦截/放行两侧）:", summary)

    rep = report()
    print("命中拦截:", [item["case"] for item in rep["blocked"]])
    print("漏拦（保留触发请求可复现）:")
    for item in rep["missed"]:
        print(f"  - {item['case']} [{item['biz']}] {item['request']}"
              f"  期望规则={item['expect_rule']}")
    print("误拦分组:", rep["false_positives"]["by_scenario"])


def stage_two_change_and_compare():
    print_header("第二段：规则改动 -> 影响预览 -> 只跑受影响用例 -> 对比")

    preview = impact(RULES_V2)
    print("执行前的变更影响范围:")
    print("  规则变更:", preview["rule_changes"])
    print("  受影响用例:", preview["affected_cases"])
    print("  按业务线分布:", preview["by_biz"])

    summary = regress(RULES_V2, only_affected=True)
    print("\n只跑受影响用例的回归汇总:", summary)

    rep = report()
    print("\n误拦用例按 业务线/业务场景 分组（附原始请求，可直接复现）:")
    for biz, scenarios in rep["false_positives"]["by_scenario"].items():
        for scenario, items in scenarios.items():
            for item in items:
                print(f"  - {item['case']} [{biz}/{scenario}] "
                      f"rule={item['blocking_rule']}  request={item['request']}")

    print("\n误拦/漏拦按规则统计:")
    for rule_id, stat in rep["by_rule"].items():
        if stat["blocked"] or stat["false_positive"] or stat["missed"]:
            print(f"  - {rule_id}: 正确拦截={stat['blocked']} "
                  f"误拦={stat['false_positive']} 漏拦={stat['missed']}")

    diff = compare()
    print("\n与改动前基线对比:")
    print("  汇总差值:", diff["summary_delta"])
    print("  新增误拦:", [item["case"] for item in diff["new_false_positives"]])
    print("  消除漏拦:", diff["resolved_missed"])


def stage_three_persistence():
    print_header("第三段：文件留存，历史结果可回溯")
    with tempfile.TemporaryDirectory() as tmp:
        store_dir = os.path.join(tmp, "waf-runs")
        configure(store_path=store_dir)
        load_cases([CASE_DIR])
        regress(RULES_V1)
        regress(RULES_V2)
        print("结果已落盘:", sorted(os.listdir(store_dir)))

        # 重新配置一个指向同一目录、同一用例集的引擎，模拟进程重启
        engine = Engine(
            store=FileStore(store_dir),
            cases=load_case_files([CASE_DIR]),
        )
        runs = engine.list_runs()
        print("重启后读到的历史轮次:",
              [(item["run_id"], item["summary"]) for item in runs])
        print("历史两轮对比仍可生成:",
              engine.compare(1, 2)["summary_delta"])
    configure()  # 还原为内存默认引擎，避免影响后续调用


if __name__ == "__main__":
    # 用按业务线维护的外部用例集替换内置用例
    load_cases([CASE_DIR])
    stage_one_baseline()
    stage_two_change_and_compare()
    stage_three_persistence()
