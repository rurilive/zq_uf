# batchrerun

登记批次的任务清单，按失败范围重跑：只补跑失败分区，成功分区跳过。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 重跑范围与锁

- 首次重跑：无分区记录，整批执行，并按任务 name 记录成功/失败分区。
- 后续重跑：只补跑失败分区，成功分区计入 skipped，分区按最新结果更新。
- 重新 register 同 id 会清空分区，下一次重跑回到整批执行。
- 同一批次加非阻塞锁：进行中的重复请求立即返回 {"status": "running"}，不重复写。
- 每次重跑追加一条历史：batch_id、第几次（rerun）、补跑条数（tasks）、跳过条数（skipped）、成功/失败名单。

## 参数与返回

- register({"id", "tasks"})：tasks 为列表，元素 {"name", "ok"}；缺 id 或 tasks 非列表抛 ValueError。
- rerun(batch_id)：未知批次抛 KeyError；正常返回 {"rerun", "skipped", "runs"}。
- rerun=本次补跑条数，skipped=跳过条数，runs=该批次累计重跑次数。
- batches["last"] 为最近一次历史记录，batches[批次id] 为当前分区。
- batches 与 history 均为纯 dict/list，可 json.dumps 序列化，便于重启后复现。
