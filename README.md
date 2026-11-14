# partitionlease

现状：分区租约只记最后持有者，没有过期和栅栏。

问题：并发写互相覆盖，崩溃后分区占着，续期出现双主。

目标：过期加栅栏，写入带栅栏值，旧值拒绝，抢占有明确错误。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
