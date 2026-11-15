"""持续入队演示：增量并入 -> 规则升级合并 -> 规则回退拆分 -> 失效清理 -> 落盘恢复。"""

import os
import tempfile

from urlnorm2.rules import builtin_rules, make_strip_query_rule
from urlnorm2.index import URLNormIndex


def show(title, index):
    view = index.size()
    print("== %s | 类=%d 链接=%d" % (title, view["classes"], view["urls"]))
    for info in index.classes():
        print("   #%d 代表=%s 成员=%s" % (
            info["class_id"], info["representative"], info["members"]))


def main():
    idx = URLNormIndex()

    print("-- 1. 持续入队（增量合并，代表固定为最早出现的 URL）")
    stream = [
        "https://shop/p/1",
        "https://Shop/p/1/",
        "https://shop/p/1#detail",
        "https://shop/p/2",
        "https://a/x?utm_source=ad&keep=1",
        "https://a/x?keep=1",
    ]
    for url in stream:
        report = idx.add(url)
        print("   add %-42s -> %s 类#%d 代表=%s" % (
            url, report["action"], report["class_id"], report["representative"]))
    show("入队完成", idx)

    print("\n-- 2. same 查询返回命中的规则")
    verdict = idx.same("https://Shop/p/1/", "https://shop/p/1")
    print("   same -> %s, 命中规则=%s@v%s" % (
        bool(verdict), verdict.rule, verdict.rule_version))

    print("\n-- 3. 规则升级：剔除 utm_source，只重算受影响类并完成合并")
    idx._normalizer.add_rule(make_strip_query_rule(["utm_source"]))
    report = idx.recompute(progress=lambda stage, d: print("   进度:", stage, d))
    print("   重算类:", report["recomputed_class_ids"], "合并:", report["merges"])

    print("\n-- 4. 规则回退：fragment 不再剥离，受影响类拆分")
    report = idx.update_rule("strip_fragment", lambda value: value)
    print("   重算类:", report["recomputed_class_ids"], "拆分:", report["splits"])
    show("规则回退后", idx)

    print("\n-- 5. 失效链接清理（代表失效时顺延最早成员，类空则删除）")
    print("  ", idx.remove("https://shop/p/1#detail"))
    print("  ", idx.remove("https://shop/p/2"))

    print("\n-- 6. 索引落盘并在新实例恢复，恢复后继续增量入队")
    current_rules = builtin_rules() + [make_strip_query_rule(["utm_source"])]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "urlnorm-index.json")
        print("   落盘:", idx.save(path))
        # 恢复需提供与快照同 id 的规则；版本不一致时只重算受影响类
        restored = URLNormIndex.load(path, rules=current_rules)
        print("   恢复:", restored.recovery)
        print("   新并入:", restored.add("https://SHOP/p/1/?z=1"))
        print("   规模:", restored.size()["class_sizes"])


if __name__ == "__main__":
    main()
