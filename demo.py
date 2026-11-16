"""演示：以回连为准判定 SSRF，不再只看响应文本。

运行：python3 demo.py
全部流量都留在本机（内嵌接收端 + 模拟目标），可以直接离线跑。
"""

import urllib.request
from urllib.parse import parse_qsl, urlsplit

from ssrfverify import (
    EmbeddedReceiver,
    HttpResponse,
    ReceiverUnavailable,
    check,
    configure,
    report,
    shutdown,
)


def show(record):
    keys = ("status", "hit", "id", "target", "callback_at", "source_address", "reason")
    print("  " + "  ".join(f"{k}={record[k]}" for k in keys))
    if record["response"] is not None:
        body = record["response"]["body"].replace("\n", " ")[:40]
        print(f"  response=[{record['response']['status']}] {body!r}")


# 一个「有 SSRF」的目标：把参数当 URL 取一次。
def vulnerable_target(url, timeout):
    payload = dict(parse_qsl(urlsplit(url).query))["id"]
    urllib.request.urlopen(payload, timeout=timeout).read()
    return HttpResponse(status=200, headers={"Content-Type": "text/plain"}, body="ok")


# 一个「没有回连但响应异常」的目标：响应里带出内网文件内容。
def echoing_target(url, timeout):
    return HttpResponse(
        status=200,
        headers={"Content-Type": "text/plain"},
        body="root:x:0:0:root:/root:/bin/bash",
    )


def section_one():
    print("== 第一段：回连命中 / 可疑项 / 去重 ==")
    receiver = EmbeddedReceiver().start()
    configure(receiver=receiver, timeout=2.0)

    show(check("http://10.0.0.5/item?id=1", "id", transport=vulnerable_target))
    # 同一 (url, param) 再次调用只累加 attempts，不会在报告里多出一条
    show(check("http://10.0.0.5/item?id=1", "id", transport=vulnerable_target))
    show(check("http://10.0.0.5/item?id=2", "id", transport=echoing_target))
    show(check("http://10.0.0.5/item?id=3", "id", transport=lambda u, t: HttpResponse(200, body="not found")))

    data = report()
    print(f"  checks={data['checks']} pairs={data['pairs']} duplicates={data['duplicates']}")
    print(f"  hits={[h['id'] for h in data['hits']]}")
    print(f"  suspicious={[(s['param'], s['reason']) for s in data['suspicious']]}")
    print(f"  接收端回连记录：{[c['id'] for c in data['callbacks']]}")
    receiver.stop()
    shutdown()


class DeadReceiver:
    """模拟接收端不可用。"""

    base_url = ""

    def available(self):
        return False

    def callbacks(self):
        return []


def section_two():
    print("\n== 第二段：接收端不可用就停止本轮，不当作安全 ==")
    configure(receiver=DeadReceiver(), timeout=2.0)
    try:
        check("http://10.0.0.5/item?id=9", "id", transport=vulnerable_target)
    except ReceiverUnavailable as exc:
        print(f"  已停止：{exc}")
    data = report()
    print(f"  round_status={data['round_status']} hits={data['hits']}")
    shutdown()


if __name__ == "__main__":
    section_one()
    section_two()
