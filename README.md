# jobwindow

现状：到点就开窗，不检查上游是否完成。

问题：上游没完成就开跑，错过窗口不补，并发窗口互相挤占。

目标：开窗先校验依赖，错过按策略补跑一次，窗口内并发有上限。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
