# csrftoken

现状：每次取令牌都重新抓，按页面覆盖保存。

问题：并发抓取让令牌立刻失效，多步骤对不上，刷新失败卡死。

目标：按页面表单步骤分域，单飞缓存，一次性消费和回退。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
