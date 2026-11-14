from datetime import datetime

from crontick import next_run, tick

print(next_run("shop", datetime(2026, 1, 1, 8, 0, 0)))
print(tick(datetime(2026, 1, 1, 8, 0, 0), lambda task: print("run", task)))

# TODO: 第二段演示（实现后补）
