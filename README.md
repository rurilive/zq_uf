# crawllease

现状：任何持有者都能领取和续约，租约没有到期。

问题：同一任务多机重复抓，崩溃后永远占用。

目标：租约带到期和版本，续约失败可抢占，释放幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
