# captchahandoff

现状：命中验证码直接抛异常，没有工单、没有超时、没有回写。

问题：任务反复重试也过不去，短信码流程一直挂着，同账号被反复要求验证。

目标：可接管的等待队列，带工单、超时、回写继续和账号互斥。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
