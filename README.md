# seedscore

现状：种子不评分，全部按入队顺序保留。

问题：低质页面先抓，重复种子占配额，失效种子仍在队列。

目标：入队前按来源和历史产出评分，同目标只留最高分。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
