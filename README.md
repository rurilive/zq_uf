# crawlcap

现状：每个站点固定四路并发，结果只记录不参与调整。

问题：慢站占住线程池，成功率不参与爬升，配额被并发绕过。

目标：按成功率阶梯爬升，失败回退，并发与站点配额联动。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
