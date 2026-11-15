"""两个演示：断线后从断点续传；文件损坏进隔离区后可重试。

全部离线运行，用 ScriptedTransport 模拟站点行为，不需要网络。
"""

import os
import tempfile

from attachcheck import AttachmentStore, ScriptedTransport

URL = "https://shop.example/contract.pdf"
CONTENT = ("合同正文\n" * 200).encode("utf-8")

site = ScriptedTransport()
site.add(URL, CONTENT, etag='"v1"')
# 第一次只发 400 字节就断开。
site.script(URL, {"cut": 400})

with tempfile.TemporaryDirectory() as tmp:
    target = os.path.join(tmp, "contract.pdf")
    store = AttachmentStore(tmp, transport=site)

    # 第一段：断线不出正式文件，隔离区留下断点。
    first = store.fetch(URL, target)
    print("断线  ok =", first["ok"], " 已下载 =", first["bytes"],
          " 目标目录有正式文件 =", os.path.exists(target),
          " 隔离记录 =", first["quarantine"])

    # 第二段：从保存的断点续传，完成后才出现正式文件。
    second = store.fetch(URL, target)
    print("续传  ok =", second["ok"], " 续传 =", second["resumed"],
          " 本次收到 =", second["received"], " 总字节 =", second["bytes"],
          " 校验 =", second["verified"]["checksum"][:12],
          " 正式文件 =", os.path.exists(target))

    # 第三段：文件被改坏，校验发现并移入隔离区。
    with open(target, "r+b") as handle:
        handle.seek(100)
        handle.write(b"BROKEN")
    checked = store.verify(target, quarantine=True)
    print("校验  ok =", checked["ok"], " 原因 =", checked["reason"],
          " 已隔离 =", checked.get("quarantine"))

    # 第四段：重试隔离区记录，重新下载并校验。
    retried = store.retry(checked["quarantine"])
    print("重试  ok =", retried["ok"], " 路径 =", os.path.basename(retried["path"]),
          " 校验通过 =", retried["verified"]["ok"],
          " 隔离区剩余 =", len(store.list_quarantine()))
