# asanparse

安全方向的内存检测报告解析小工具：把几百行的 AddressSanitizer 报告按结构拆成问题、调用栈和分配位置，同一处问题按栈归并，地址能映射到源码的给出文件、行号和函数名。纯标准库，无外部依赖。

## 入口

- `parse(report, error_type=None, symbols=None)`：解析一份报告，返回 `issues`、`count`、`kept`（识别出的报告段数）、`occurrences`、`mapped` / `unmapped`、`types`、`status`、`rejected`、`raw` 和本次的 `report` 编号。归并在模块内进行，同一处问题多次 parse 合并到一个条目；`error_type` 按类型过滤（支持 `heap-*` 通配），`symbols` 传地址到源码的符号表，给没带 `file:line` 的帧补源码位置。
- `locate(id)`：按条目编号取归并结果，取不到返回空字典。
- `report(error_type=None)`：汇总归并条目，返回 `issues`、`occurrences`、`types`、`groups`、`mapped` / `unmapped`、`address_changed`、`reports`，可按类型过滤。
- `reset()`：清空内存态，测试和演示重复运行时用。

## 条目字段

`id`、`type`、`address`、`addresses`、`frames`（`function` / `file` / `line` / `column` / `module` / `mapped` / `raw`）、`source`（首个能定位到源码的帧）、`sources`（各次运行里的源码位置，去重）、`alloc` / `free`（分配和释放位置）、`access`（`op` / `size` / `thread`）、`mapping`（`mapped` 或 `unmapped`）、`occurrences`（每次报告的出现记录，带各自地址和原始 `raw`）、`reports`（报告编号）、`address_changed`、`address_diff`、`count`、`group`；泄漏条目另有 `leak`（`kind` / `bytes` / `total_bytes`）。

## 归并与映射

归并只比较问题类型和调用栈的函数名，地址和行号都不参与，所以同一处问题在多次运行里地址、行号变化仍归到一起；`address_diff` 按地址列出出现次数、对应的报告编号和映射到源码的文件、行号、函数名。帧上带 `file:line`、或地址在 `symbols` 里查得到就算映射成功；都查不到时条目和帧都标 `unmapped`，不会猜一个位置。

## 解析失败

报告结构对不上时不返回空的假结果：`status` 为 `failed`（没识别出任何条目）或 `partial`（部分行没认出来），`raw` 保留原文，`rejected` 逐条列出认不出的行和原因。`parse("")` 单独返回 `status="empty"`。

支持 ASan 常见输出（`==pid==` 前缀、`READ/WRITE of size`、`freed by` / `previously allocated by` / `allocated from`、`SUMMARY`、LeakSanitizer 的多条泄漏）和简化的 `ERROR: <类型> <地址>` / `#N ...` / `ALLOC ...` 格式。

## 运行

`python3 demo.py` 跑两段演示；`python3 -m unittest discover -s tests` 跑测试。
