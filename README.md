# ckptgen

现状：一个任务一个JSON，写入直接覆盖，generation参数无效。

问题：写一半崩溃就没有可用状态，旧代际回不去，并发写互相踩踏。

目标：多代际原子写，保留最近若干代，损坏回退。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
