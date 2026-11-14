# filededup

内容寻址 + 引用计数的文件级去重回收，纯 Python 标准库。

## 对象口径

- 对象：文件内容按 SHA-256 哈希存一份，位于 `<root>.objects/<hash>`。
- 同内容只存一个对象；`add(path)` 在 `<root>/` 下建指向对象的硬链接。

## 引用口径

- 每个 `add(path)` 记一条引用，`remove(path)` 只减引用，归零才删对象。
- 增删查由全局锁串行化，并发下不会删到仍被引用的对象。
- `objects(root)` 列出仍被引用的对象，`refs(hash, root)` 按对象查引用路径。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
