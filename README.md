# siteprobe

现状：请求单个页面，记录状态码、字节数和内容类型。

问题：看不出分页、详情结构、编码和登录要求。

目标：多页并发探测、分页推断、登录识别和打分报告。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
