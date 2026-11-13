# formdiscover

现状：单页表单解析，只收可见输入框，隐藏字段和提交按钮丢弃。

问题：字段名变化填不进去，CSRF漏掉提交被拒，多步骤只抓第一步。

目标：通用表单发现，识别隐藏字段和token，按跳转继续多步骤。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
