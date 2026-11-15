"""批量演示：并发探测 + 缓存 + 临时/永久分流 + 落盘 + 续跑。"""
import os
import time

from crawlprobe2 import (aggregate, changes, configure, probe, reset, verdict,
                         wait_idle)

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".crawlprobe2-demo")

SITES = ["shop.example.com", "news.example.com", "blog.example.com",
         "api.example.com", "cdn.example.com", "docs.example.org",
         "flaky.example.org", "gone.example.org", "slow.example.net"]


def fake_prober(site, timeout):
    """模拟探测：gone 永久 404，flaky 临时 503（第二轮恢复），slow 较慢。"""
    if site.startswith("gone"):
        return "permanent", "HTTP 404 (site gone, permanent)"
    if site.startswith("flaky") and fake_prober.round == 0:
        return "temporary", "HTTP 503 (server busy, retryable)"
    if site.startswith("slow"):
        time.sleep(0.5)
    return "ok", "HTTP 200"


fake_prober.round = 0


def show(tag, out):
    print("\n== %s ==" % tag)
    for row in out["results"]:
        flag = "cache" if row["cached"] else "probe"
        print("  %-18s %-10s %-5s %s"
              % (row["site"], row["verdict"], flag, row["reason"]))
    print("  通过 %d / 临时失败 %d / 永久失败 %d / 缓存命中 %d"
          % (out["ok"], out["temporary"], out["permanent"], out["cache_hits"]))


def main():
    reset()
    configure(prober=fake_prober, state_dir=STATE_DIR,
              ttl=2.0, temp_ttl=0.5, timeout=5.0)

    show("第一批（全部实探）", probe(SITES, workers=4))
    show("第二批（全部命中缓存）", probe(SITES, workers=4))

    time.sleep(2.2)  # 等通过结果过期：旧结论先返回，后台刷新
    show("第三批（缓存过期，旧结论 + 后台刷新）", probe(SITES, workers=4))
    wait_idle(5)

    fake_prober.round = 1  # flaky 恢复
    time.sleep(0.6)        # 临时失败到重试间隔
    show("第四批（flaky 重试后恢复）", probe(SITES, workers=4))

    print("\n== 单站结论 ==")
    print(verdict("gone.example.org"))
    print(verdict("flaky.example.org"))

    print("\n== 按域名聚合 ==")
    for domain, b in sorted(aggregate(probe(SITES, workers=4)).items()):
        print("  %-18s total=%d ok=%d temporary=%d permanent=%d cached=%d"
              % (domain, b["total"], b["ok"], b["temporary"], b["permanent"],
                 b["cached"]))

    print("\n== 结论变更记录 ==")
    for c in changes():
        print("  %(site)s: %(old)s -> %(new)s (%(reason)s)" % c)

    print("\n落盘目录：%s" % STATE_DIR)
    print("  verdicts.json 当前结论（重启后恢复=续跑）  probes.jsonl 探测项")
    print("  changes.jsonl 结论变更                     progress.json 批量进度")


if __name__ == "__main__":
    main()
