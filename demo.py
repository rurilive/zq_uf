"""演示：上游逐渐变慢且偶发失败，执行器降并发并断点重试。"""

import time

from backfillplan import plan, run


class SlowUpstream:
    """模拟上游：越调越慢，中途失败一次。"""

    def __init__(self):
        self.calls = 0
        self.failed = False

    def fetch(self, shard, checkpoint, limit):
        self.calls += 1
        if not self.failed and self.calls > 6:
            self.failed = True
            raise RuntimeError("upstream 500")
        time.sleep(0.002 * self.calls)
        start = shard["start"] if checkpoint is None else checkpoint + 1
        return list(range(start, min(shard["end"], start + limit)))


p = plan({"start": 1, "end": 200,
          "time_start": 0, "time_end": 7200, "time_step": 3600}, 3)
print("分片:")
for s in p["shards"]:
    print(" ", s["id"], "keys", s["start"], "-", s["end"],
          "deps", s["deps"], "prio", s["priority"])

summary = run(p, 6, upstream=SlowUpstream(),
              batch_size=20, latency_threshold=0.02)
print("并发变化:", summary["concurrency"])
print("分片状态:")
for s in summary["shards"]:
    print(" ", s["id"], s["status"], "断点", s["checkpoint"],
          "重试", s["retries"])
print("完成", summary["done"], "剩余", summary["remaining"],
      "写入", summary["written"], "重复", summary["duplicates"])
print("(两个时间桶扫同一主键段，重复键被 sink 去重，体现幂等)")
