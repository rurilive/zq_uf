# queuefresh

现状：按入队时写死的优先级排序，同优先级先进先出。

问题：新鲜度只算一次，等待久了不提权，高优先级会饿死低优先级。

目标：按新鲜度衰减和等待时间动态排序，带防饿死保底和分数输出。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
