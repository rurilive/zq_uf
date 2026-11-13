# langdetect

现状：按字符集二分，有CJK算中文，其余算英文，置信度恒为1。

问题：日文和中文混淆，法语英语都算拉丁，短文本和混合页面直接判错。

目标：字符区间、常见词和停用词打分，支持多语言、短文本和多候选。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
