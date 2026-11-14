# crawlband

现状：全局固定速率，任务共用额度。

问题：大文件占满带宽，突发无上限，结束不回收。

目标：按任务限速，突发上限，借还和结束归还。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
