# linkguard

现状：请求一次并原样返回响应。

问题：重定向环不判，会话参数反复入队，跨域不记录。

目标：规范化判环、参数归并、跨域策略和次数上限。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
