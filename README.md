# fairqueue

现状：全局先进先出队列，重试任务回到队尾，不区分站点。

问题：大站一次提交上万条，小站长期排不到；慢站占满并发池。

目标：按站点权重的公平队列，重试回原站点队列，输出等待统计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
