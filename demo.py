"""模拟采集批次重放：入账、崩溃、修复对账、幂等重放。"""

import hashlib
import os
import shutil
import tempfile

import crawlledger
from crawlledger import append, ledger_size, verify

LEDGER = os.path.join(tempfile.gettempdir(), "crawlledger-demo")
shutil.rmtree(LEDGER, ignore_errors=True)
crawlledger.configure(LEDGER)

BATCH = "crawl-001"


def snap(page):
    return hashlib.sha256(("snapshot:" + page).encode()).hexdigest()


print("== 1. 采集入账：5 个页面，带快照校验和 ==")
snapshots = {}
for i in range(1, 6):
    page = "page-%d" % i
    snapshots[page] = snap(page)
    print(append({"batch": BATCH, "seq": i, "page": page, "snapshot": snap(page)}))

print("\n== 2. 模拟崩溃：账本尾部留下半行 ==")
with open(os.path.join(LEDGER, "batch-%s.jsonl" % BATCH), "a", encoding="utf-8") as fh:
    fh.write('{"type": "entry", "batch": "%s", "seq": 6, "page": "pa' % BATCH)

print("\n== 3. 重启恢复：校验发现半行并修复，与页面快照对账 ==")
crawlledger.reset()  # 模拟进程重启，从磁盘恢复
print(verify(BATCH, snapshots=snapshots))

print("\n== 4. 重放同一批次：全部判重，不重复记账 ==")
for i in range(1, 6):
    page = "page-%d" % i
    print(append({"batch": BATCH, "seq": i, "page": page, "snapshot": snapshots[page]}))

print("\n== 5. 恢复后已完成的页面与账本大小 ==")
print(verify(BATCH)["pages"])
print(ledger_size())
