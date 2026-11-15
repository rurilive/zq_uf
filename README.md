# snapdiff

现状：整页取哈希，变化只看布尔值。

问题：广告和计数器造成误报，变更无法定位，历史只留一份。

目标：按块取指纹，变更定位到块，快照按版本保留。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
