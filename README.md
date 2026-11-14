# ckptgen

多代际检查点：每个任务一个目录，每代际一个 `gen-NNNNNN.json`，
文件内含代际号、状态校验和与状态本体，纯标准库实现。

写入口径：先写同目录临时文件并 fsync，再原子改名；改名前崩溃，
旧代际完好，临时文件自动清理。同一任务的并发写按任务锁串行化。

保留口径：默认保留最近 3 代（`save(..., keep=N)` 可调），超出按
最旧优先清理；正在被读取的代际跳过不删，留给下次清理。

读取口径：`load(task, generation)` 中 generation=1 为最新一代，
2 为上一代，省略读最新。目标代际损坏（校验和不符或 JSON 非法）
时跳过并回退到更旧一代，回退记入 `ckptgen.fallbacks()`。
`save` 返回当前代际号、保留列表和文件路径。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
