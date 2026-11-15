# crawlmerge2

现状：按批次顺序写入，同键后到的行直接覆盖。

问题：版本和来源不记录，冲突静默丢失，结果无法回放。

目标：按版本和来源合并，同版本保留两侧，结果可回放。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
