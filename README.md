# asanparse

安全方向的内存检测报告解析小工具，纯标准库。按报告结构提取错误类型、出错地址、
调用栈和分配位置；同一处问题按调用栈归并，多次运行/多份报告合并展示；地址映射到
源码时给出文件、行号和函数名，映射不上时显式标 `unmapped`；格式变化导致解析失败
时保留原文，不静默返回空结果。

## 入口

- `parse(text, report_name=None)`：解析一份报告，返回 `issues`（本次问题条目）、
  `groups`（归并条目）、`kept`（解析失败但保留原文的片段）和 `count`。多次调用会
  累积进同一注册表，同一处问题自动并入同一个 group；`clear()` 可重置。
- `locate(id)`：按 id 取条目，`issue-*`（单次报告记录）、`group-*`（归并条目）、
  `kept-*`（原文留档）均可，取不到返回 `{}`。
- `groups(issue_type=None)`：归并后的条目，可按问题类型筛选，便于批量修复。
- `report(issue_type=None)`：类型计数、归并条目数、映射状态（mapped/unmapped）和
  留档片段数，同样支持按类型筛选。
- `kept()`：所有解析失败、保留原文的片段。

## 数据约定

- 帧结构：`index / address / function / source{file,line,column} / mapping / raw`。
  `source` 为 `None` 且 `mapping == "unmapped"` 表示该地址无法映射到源码（如 libc
  内部帧），不会留空让调用方猜。
- 归并键：`错误类型 + 调用栈签名`（优先 `函数@文件:行`，无源码时退到帧地址）。
- group 的 `addresses` 单独列出同一问题在不同报告里的地址差异，每条带映射状态和
  文件/行号/函数名。
- 兼容两种报告格式：精简工具格式（`ERROR:`/`#n`/`ALLOC`）和真实
  AddressSanitizer/LeakSanitizer 输出（含 freed/allocated 分段、SUMMARY 行、
  Direct/Indirect leak 段）。
