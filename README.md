# crawlbudget

现状：全局一个请求上限，站点参数被忽略，超额直接抛异常。

问题：大站吃光额度，小站跑不到；各进程各记一份，合计远超上限。

目标：按站点和任务分配预算，支持日额度和突发额度，共享原子扣减。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
