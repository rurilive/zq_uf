# hostmemo

现状：探测结果写进字典，读取时仍然每次返回默认画像。

问题：重复探测慢，站点改版后记忆不失效，多进程各自探测。

目标：按站点记忆请求头和编码，带版本与过期，多进程共享。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
