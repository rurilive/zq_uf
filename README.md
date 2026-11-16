# corpusmin

镜像组件清单：逐层记录新增/删除，合并后以最终文件系统为准。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

- `sbom(image)`：返回 `components`（同名只留一条，含 `version`/`license`/`layer`/`layers`）、`layers`（每层 `added`/`removed`/`replaced`）、`digest`、`scanned_at`、`diff`（相对同镜像上次扫描）、`warnings`。
- 层从基础到最上处理；更高层同名组件覆盖版本与许可证（更高层没给版本/许可证则沿用低层），`remove` 不抹掉记录，保留 `status="removed"` 并用 `removed_in` 标出删除层，`add` 可再次复活。
- 摘要未显式给出时按镜像名+层内容确定性计算（`sha256:...`），同内容重复扫描稳定；`scanned_at` 默认当前 UTC。
- `report()` 汇总所有镜像（组件数按镜像去重）；`report("img")` 看单镜像；`report("a","b")` 按组件名+版本对齐，`added`/`removed`/`version_changed` 分开统计。
- 摘要相同但组件不同、或组件记录摘要与本次镜像摘要不一致，记入 `warnings`。
