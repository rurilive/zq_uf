# crawlwarm

现状：冷启动直接开满并发。

问题：站点被瞬间打挂，慢站一直失败，抖动后不回退。

目标：阶梯预热，成功率反馈，回退冷却和重启恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
