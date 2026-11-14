"""模拟两个采集进程共享同一份许可名单。"""
import multiprocessing as mp
import os
import tempfile
import time

STORE = os.path.join(tempfile.mkdtemp(prefix="siteallow-demo-"), "allowlist.json")
os.environ["SITEALLOW_STORE"] = STORE


def crawler(tag):
    """采集进程：每轮先查名单再决定抓不抓 shop。"""
    from siteallow import allowed

    for _ in range(6):
        ok = allowed("shop", task="crawler")
        print("[进程%s pid=%d] shop: %s"
              % (tag, os.getpid(), "允许抓取" if ok else "已拉黑，跳过"), flush=True)
        time.sleep(0.2)


def main():
    from siteallow import audit, block, current, unblock

    pa = mp.Process(target=crawler, args=("A",))
    pb = mp.Process(target=crawler, args=("B",))
    pa.start()
    pb.start()

    time.sleep(0.3)
    print("\n[主控] 拉黑 shop 0.6 秒（任务范围 crawler）", flush=True)
    block("shop", "HTTP 403 反爬", 0.6, task="crawler")
    time.sleep(0.8)  # 等到期自动解除

    print("[主控] 再次拉黑，随后人工解除", flush=True)
    block("shop", "频率过高", 60, task="crawler")
    time.sleep(0.3)
    unblock("shop", task="crawler", reason="人工复核为误封")

    pa.join()
    pb.join()

    print("\n当前名单:", current() or "（空）")
    print("\n审计记录:")
    for rec in audit(host="shop"):
        print("  %s %-7s task=%-8s 原因=%s pid=%d"
              % (time.strftime("%H:%M:%S", time.localtime(rec["ts"])),
                 rec["event"], rec["task"], rec["reason"], rec["pid"]))


if __name__ == "__main__":
    main()
