import importlib
import tempfile

from partclone import clone, verify

_impl = importlib.import_module("partclone.clone")
_impl._configure(tempfile.mkdtemp(prefix="partclone-demo-"))

print(clone({"tag": "orders-1", "rows": 120}))
print(verify("orders-1"))

# 第二段：模拟克隆中断 → 断点续传 → 校验
data = [{"order_id": i, "amount": i * 10} for i in range(120)]
source = {"rows": data, "readonly": False}

interrupted = clone({
    "tag": "orders-2",
    "data": data,
    "source": source,
    "fail_at": 80,      # 复制到第 80 行时中断
    "fail_times": 1,
    "max_retries": 1,   # 本次调用不重试，断点落盘
})
print(interrupted)      # failed：半成品目标已清理，断点保留在 checkpoints/

resumed = clone({"tag": "orders-2", "data": data, "source": source})
print(resumed)          # resumed=True，从第 80 行继续，完成后目标对查询可见
print(verify("orders-2"))
