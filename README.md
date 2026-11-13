# loginflow

现状：顺序执行步骤，上下文用字典传递。

问题：验证码卡住，步骤换序失败，多账号共用会话。

目标：步骤声明、回调注入、会话隔离和失败原因。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
