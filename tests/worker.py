"""测试用的子进程工人：在独立进程里用传入的标识记录一个阶段。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlchain import start

task, trace_id, attempt, data_dir = sys.argv[1:5]
tr = start(task, trace_id=trace_id, attempt=int(attempt), data_dir=data_dir)
with tr.stage("fetch"):
    pass
print(f"worker pid={os.getpid()}")
