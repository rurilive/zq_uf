"""链路标识验证：一个采集任务，跨进程和重试都能用同一个标识串起来。"""

import json
import os
import shutil
import subprocess
import sys
import time

from crawlchain import cleanup, export, export_detail, format_replay, query, start

TASK = "task-a"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".crawlchain-demo")


def run_fetch_cross_process(trace_id, attempt):
    """fetch 阶段放到子进程里跑，标识通过命令行传过去。"""
    subprocess.run(
        [sys.executable, "demo_worker.py", TASK, trace_id, str(attempt), DATA_DIR],
        cwd=os.path.dirname(os.path.abspath(__file__)), check=True,
    )


def crawl_once(tr, fail_parse):
    run_fetch_cross_process(tr["trace"], tr["attempt"])
    try:
        with tr.stage("parse"):
            time.sleep(0.02)
            if fail_parse:
                raise ValueError("bad html: missing <title>")
        with tr.stage("store"):
            time.sleep(0.15)  # 模拟慢入库
    except ValueError as exc:
        tr.finish(ok=False, error=str(exc))
        return False
    tr.finish(ok=True)
    return True


def main():
    shutil.rmtree(DATA_DIR, ignore_errors=True)

    tr = start(TASK, data_dir=DATA_DIR)
    print("第 1 次（parse 会失败）:", crawl_once(tr, fail_parse=True))

    retry = start(TASK, trace_id=tr["trace"], data_dir=DATA_DIR)  # 重试沿用同一标识
    print("第 2 次（重试成功）:", crawl_once(retry, fail_parse=False))

    report = query(tr["trace"], data_dir=DATA_DIR, slow_ms=100)
    print("\n== 链路报告 ==")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    print("\n== 导出 ==")
    print("汇总:", export(tr["trace"], data_dir=DATA_DIR))
    print("明细:", export_detail(tr["trace"], data_dir=DATA_DIR))

    print("\n== 回放 ==")
    print(format_replay(tr["trace"], data_dir=DATA_DIR))

    print("\n== 清理（默认保留最近一天）==")
    print(cleanup(data_dir=DATA_DIR))


if __name__ == "__main__":
    main()
