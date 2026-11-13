"""跑一批混合采集任务，打印分阶段汇总，并演示跨进程合并与重启累加。"""

import os
import random
import tempfile

from crawlmetrics import Stats

SITES = ("a.com", "b.com", "c.com")


def run_batch(seed, rounds):
    """模拟一个采集进程：三类阶段各有成功、失败和重试。"""
    rng = random.Random(seed)
    stats = Stats()
    for index in range(rounds):
        site = SITES[index % len(SITES)]
        task = "crawl-{}".format(index % 2)

        if rng.random() < 0.3:
            stats.inc("request.retry", site=site, task=task)
        if rng.random() < 0.2:
            stats.inc("request.fail", site=site, task=task)
            stats.timing("request", rng.uniform(0.05, 0.5), site=site, task=task)
            continue
        stats.inc("request.success", site=site, task=task)
        stats.timing("request", rng.uniform(0.02, 0.2), site=site, task=task)

        if rng.random() < 0.15:
            stats.inc("parse.fail", site=site, task=task)
            stats.timing("parse", rng.uniform(0.01, 0.1), site=site, task=task)
            continue
        stats.inc("parse.success", site=site, task=task)
        stats.timing("parse", rng.uniform(0.005, 0.05), site=site, task=task)

        if rng.random() < 0.1:
            stats.inc("storage.fail", site=site, task=task)
        else:
            stats.inc("storage.success", site=site, task=task)
        stats.timing("storage", rng.uniform(0.01, 0.2), site=site, task=task)
    return stats


def print_summary(title, snapshot):
    print("== {} ==".format(title))
    print("阶段计数 (total / 按站点):")
    for name in sorted(snapshot.get("counters", {})):
        counter = snapshot["counters"][name]
        sites = ", ".join(
            "{}={}".format(site, value) for site, value in counter["sites"].items()
        )
        print("  {:<18} total={:<3} {}".format(name, counter["total"], sites))
    print("耗时分位数 (秒):")
    for stage in sorted(snapshot.get("timers", {})):
        timer = snapshot["timers"][stage]
        quantiles = timer["quantiles"]
        print(
            "  {:<18} n={:<3} p50={:.3f} p90={:.3f} p95={:.3f} p99={:.3f}".format(
                stage, timer["count"], quantiles["p50"], quantiles["p90"],
                quantiles["p95"], quantiles["p99"],
            )
        )


def main():
    worker_one = run_batch(seed=1, rounds=20)
    worker_two = run_batch(seed=2, rounds=15)
    merged = Stats.merge_snapshots(
        [worker_one.snapshot(), worker_two.snapshot()]
    )
    print_summary("跨进程合并 (2 个采集进程, 共 35 个任务)", merged)

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "stats.json")
        Stats(merged).dump(path)
        restarted = Stats.load(path)
        extra = run_batch(seed=3, rounds=10)
        restarted.merge(extra.snapshot())
        print_summary("重启后继续累加 (再跑 10 个任务)", restarted.snapshot())
        print("快照文件: {} ({:.1f} KiB, JSON 可序列化)".format(
            path, os.path.getsize(path) / 1024
        ))


if __name__ == "__main__":
    main()
