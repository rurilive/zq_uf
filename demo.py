from crawlband import give_back, take

print(take("task-1", 100))
print(give_back("task-1", 40))

# 第二段：大文件与小任务 —— 按任务限速 + 同组借还
from crawlband import band, finish, stats

band.reset()
band.CONFIG.update(base_rate=200, burst_limit=700,
                   borrow_limit=500, borrow_ttl=5.0)

take("small-1", 50)           # 小任务各占一点，空闲额度可借出
take("small-2", 50)
take("big-file", 200)         # 大文件先占自有基础速率
print(take("big-file", 300))  # 突发：借 small-1/small-2 的空闲额度
try:
    take("big-file", 300)     # 超突发上限 700，拒绝
except RuntimeError as exc:
    print("rejected:", exc)
print(stats())
give_back("big-file", 300)    # 归还借入
for task in ("big-file", "small-1", "small-2"):
    finish(task)              # 任务结束，归还全部
print(stats())
