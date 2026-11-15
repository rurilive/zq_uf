# pagefinger

现状：整页文本取 SHA256，空白和脚本都会改变指纹。

问题：改个广告指纹就变，相似页面判不出来，阈值写死。

目标：剔除脚本、样式和广告，按段落分块取稳定块，短页面降阈。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
