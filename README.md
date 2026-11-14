# snapshotgc

快照链回收模块：引用计数 + 租约，纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 引用计数口径
- 块按引用计数管理：快照创建/备份开始时引用父块 +1，删除快照只减引用。
- 只有 committed 且 refs==0 的块才回收；父删后子快照照常可读。
- 并发备份的新块先登记（registered）再写入，回收跳过已登记未完成的块。
- gc(keep) 保留最新 keep 个快照，返回 {removed, skipped, freed}。

## 租约口径
- pin(tag, ttl) 给快照加租约，租约期内 gc 不删该快照及其块。
- 租约到期自动失效；pin 不存在的快照返回 pinned=False。

## 中断续跑
- 回收前先把候选块标记进 gc_pending 并落盘，再逐块删除。
- 中断后重启 reset(同一目录) 再调 gc，从剩余标记继续，不误删。
