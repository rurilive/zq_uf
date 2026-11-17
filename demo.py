"""演示：蜜罐访问行为聚类的完整一轮。

直接运行：python3 demo.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from honeypot import adjust, cluster, load_adjustments, report, reset, save_adjustments


def scanner(ip, paths, ua, day, start=100):
    return [
        {"ip": ip, "path": path, "at": start + i, "ua": ua, "day": day}
        for i, path in enumerate(paths)
    ]


def main():
    reset()

    # 同一批 nikto 扫描换了三个地址；gobuster 是另一批；
    # 另有一个地址只打了一个内部接口，疑似针对性探测。
    events = []
    for ip in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
        events += scanner(ip, ["/admin"] * 6 + ["/login"] * 2, "nikto/2.1.6",
                          "2026-11-16", start=100)
    # 针对性探测藏在批量扫描来源里：同地址打完 /admin 后摸了内部接口。
    events.append({"ip": "203.0.113.12", "path": "/internal/api/users",
                   "at": 108, "ua": "nikto/2.1.6", "day": "2026-11-16"})
    events += scanner("198.51.100.20", ["/.env", "/wp-config.php", "/backup.zip"],
                      "gobuster/3.1", "2026-11-16", start=400)
    events.append({"ip": "192.0.2.55", "path": "/internal/api/users",
                   "at": 700, "ua": "curl/8.4", "day": "2026-11-16"})
    # 第二天 nikto 换了新地址继续扫，验证跨天仍归入同一行为组。
    events += scanner("203.0.113.99", ["/admin"] * 5, "nikto/2.1.6",
                      "2026-11-17", start=1000)

    out = cluster(events)
    print("=== 第一轮：%d 条事件、%d 个地址聚成 %d 组 ==="
          % (out["events"], out["sources"], out["groups_count"]))
    for group in report()["top"]:
        print("- %s %s | 地址 %d 个 | 事件 %d 条 | %s ~ %s"
              % (group["id"], group["label"], group["sources"], group["events"],
                 group["first"], group["last"]))
        tools = ", ".join(f["name"] for f in group["features"]["tools"]) or "无工具指纹"
        print("  工具特征: %s" % tools)

    print("\n=== 针对性探测（单地址组 + 离群事件）===")
    print("单地址组:", out["singletons"])
    for item in out["outliers"]:
        print("- %s 命中 %s，离群原因: %s"
              % (item["ip"], item["path"], "/".join(item["reasons"])))

    print("\n=== 按天汇总（看批量扫描变化）===")
    for row in report()["daily"]:
        print("- %s: %d 组 / %d 地址 / %d 事件"
              % (row["day"], row["groups"], row["sources"], row["events"]))

    # 人工调整：把 nikto 与 gobuster 两个组合并并命名，落盘后下一轮自动套用。
    nikto = next(g for g in out["groups"] if "nikto" in g["label"])
    gobuster = next(g for g in out["groups"] if "gobuster" in g["label"])
    nikto_sig = nikto["signature"]
    adjust("merge", sources=[gobuster["signature"]], into=nikto_sig,
           label="web-scanner-crew")
    # 预登记：第二天出现的新地址若行为匹配不上，按地址钉进已命名的组。
    adjust("pin", ip="203.0.113.99", group="web-scanner-crew")

    tmpdir = tempfile.mkdtemp(prefix="honeypot-adjust-")
    path = os.path.join(tmpdir, "adjustments.json")
    save_adjustments(path)
    print("人工调整已落盘: %s" % path)

    # 第二轮：重新聚类（模拟新一天的数据），调整自动生效。
    reset()
    load_adjustments(path)
    second = cluster(events)
    print("\n=== 第二轮：套用人工调整后剩 %d 组 ===" % second["groups_count"])
    for group in report()["top"]:
        print("- %s %s | 地址 %d 个 | 事件 %d 条"
              % (group["id"], group["label"], group["sources"], group["events"]))


if __name__ == "__main__":
    main()
