# incbackup

现状：每次备份复制整个目录，快照之间没有引用关系。

问题：仓库很快翻倍，内容没变也重新拷，恢复按顺序依赖容易断链。

目标：内容哈希分块的增量备份，父快照链，任意点恢复和链断裂拒绝。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
