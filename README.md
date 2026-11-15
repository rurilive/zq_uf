# crawlhold

采集任务的可恢复挂起模块，纯 Python 标准库，接口 `hold(task)` / `release(task)`。

## 挂起阶段

`stopping`(停止取新任务) → `draining`(等在跑请求收尾，超时强制收尾并标记半写文件，未完成 URL 重新入队) → `checkpointing`(原子落检查点) → `releasing`(释放资源) → `exited`。

## 资源口径

- 代理、配额：挂起时归还，在报告的 `released` 中列出；文件句柄同时关闭。
- 半写文件：挂起时记入检查点 `partial_files`，恢复时删除（`cleaned`）。
- 检查点：`<workdir>/<task>.checkpoint.json`，含进度与待抓队列；恢复先校验（版本、任务名、字段）再消费，进度与挂起前一致。
- 重复 `hold` / `release` 幂等，返回首次结果。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
