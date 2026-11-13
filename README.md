# confdrift

现状：抓当前配置存快照，只报告两边都有的键的值变化。

问题：新增和删除看不出，敏感项明文比对，不同环境互相覆盖。

目标：多环境漂移检测，四类差异分级，敏感项只比哈希，重复告警合并。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
