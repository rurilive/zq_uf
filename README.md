# readclean

现状：按标签切分网页文本，输出标题和段落。

问题：导航和评论混入正文，短页面被切碎，表格内容丢失。

目标：可配置的清洗链，带打分、阈值和置信度。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
