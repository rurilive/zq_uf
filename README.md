# filededup

内容寻址的文件去重与引用回收模块，纯 Python 标准库。

## 对象与引用口径

- 对象：文件内容的 SHA-256 摘要，存于 `store/objects/<sha256>`，相同内容全仓库只有一个对象。
- 引用：一个路径对一个对象的登记，记在 `store/index.json`（`{路径: 对象id}`）；同一对象出现几次，引用计数就是几。
- `add(path)`：内容未见过则新增对象，已存在则复用；该路径登记为对象的一条引用。返回 `action`（`added`/`reused`/`remapped`）、`object_id`、`object_path`、`references` 和 `reclaimed`。
- `remove(path)`：只摘除该路径的引用并减计数；计数归零才删除对象文件（id 进入 `reclaimed`），仍被引用时对象保留、`references` 给出剩余路径。
- `references(object_id=...)`（或 `path=...`）：按对象反查全部引用路径；`report()` 输出仍被引用的对象（含 `refcount`/`references`）与无引用的 `orphans`。
- 并发：同一仓库的 `add`/`remove` 由进程内线程锁加 root 目录文件锁串行化，不会删掉正在被其他路径使用的对象。

## 快速验证

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
