import json

from idemwrite import IdemWriteError, reset, write


def show(title, result):
    print(title)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print("-" * 40)


reset()

# 1. 首次带版本写入，随后客户端超时重试：同一幂等键直接重放，不重复写
first = write(
    [{"key": "order-1", "status": "paid", "version": 1}],
    idem_key="req-001",
)
show("首次提交 written=1:", first)

retry = write(
    [{"key": "order-1", "status": "paid", "version": 1}],
    idem_key="req-001",
)
show("同键重试 idem_hits=1，返回首次结果:", retry)

# 2. 新版本正常推进
newer = write([{"key": "order-1", "status": "shipped", "version": 2}])
show("version 2 覆盖成功:", newer)

# 3. 旧版本迟到：拒绝覆盖，整批回滚并记录两侧值与原因
stale = write(
    [
        {"key": "order-1", "status": "paid", "version": 1},
        {"key": "order-2", "status": "created", "version": 1},
    ]
)
show("旧版本批量写入，全批回滚 rolled_back=2:", stale)

# 4. 异常路径：缺少主键，抛出明确错误
try:
    write([{"status": "paid"}])
except IdemWriteError as exc:
    print("异常路径: %s" % exc)
