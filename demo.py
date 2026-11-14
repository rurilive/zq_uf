"""crawlprobe 离线演示：三类站点的预检、缓存与拦截。

不发出任何真实请求，全部由 DemoGateway 模拟。运行：

    python3 demo.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawlprobe import (
    CertInfo,
    HTTPResponse,
    ProbeEngine,
)


class DemoGateway:
    """按主机名路由的假网关，模拟健康站、robots 禁止站和封禁站。"""

    def __init__(self):
        healthy_cert = CertInfo(
            subject="healthy.example",
            issuer="Demo CA",
            not_before=datetime.now(timezone.utc) - timedelta(days=10),
            not_after=datetime.now(timezone.utc) + timedelta(days=80),
        )
        self._ips = {
            "healthy.example": "203.0.113.10",
            "no-robots.example": "203.0.113.20",
            "banned.example": "203.0.113.30",
        }
        self._cert = {
            "healthy.example": healthy_cert,
            "no-robots.example": healthy_cert,
            "banned.example": healthy_cert,
        }
        self._routes = {
            # 站点一：一切正常
            "https://healthy.example/robots.txt": self._resp(
                "https://healthy.example/robots.txt", 200,
                b"User-agent: *\nAllow: /\n"),
            "https://healthy.example/": self._resp(
                "https://healthy.example/", 200,
                b"<html><body>welcome to the shop</body></html>"),
            # 站点二：robots 全站禁止
            "https://no-robots.example/robots.txt": self._resp(
                "https://no-robots.example/robots.txt", 200,
                b"User-agent: *\nDisallow: /\n"),
            "https://no-robots.example/": self._resp(
                "https://no-robots.example/", 200, b"private site"),
            # 站点三：首页返回封禁特征（403 + WAF 头 + 封禁正文）
            "https://banned.example/robots.txt": self._resp(
                "https://banned.example/robots.txt", 200,
                b"User-agent: *\nAllow: /\n"),
            "https://banned.example/": self._resp(
                "https://banned.example/", 403,
                b"<html>Access Denied. Your IP has been blocked.</html>",
                headers={"X-WAF": "blocked", "Server": "cloudflare"}),
        }

    @staticmethod
    def _resp(url, status, body, headers=None):
        return HTTPResponse(
            url=url,
            status=status,
            headers={k.lower(): v for k, v in (headers or {}).items()},
            body=body,
        )

    def resolve(self, host):
        return [self._ips[host]]

    def tls_probe(self, host, port=443):
        return self._cert[host]

    def http_get(self, url):
        return self._routes[url]


def print_result(title, result):
    print(f"\n=== {title}: {result['site']} ===")
    print(f"结论 verdict={result['verdict']}  ok={result['ok']}  "
          f"cached={result['cached']}")
    for check in result["checks"]:
        line = f"  - [{check['severity']:5}] {check['check']:8} {check['detail']}"
        if not check["ok"] and "error" in check:
            line += f"（错误: {check['error']}）"
        print(line)
    for reason in result["reasons"]:
        print(f"  原因: {reason}")


def main():
    engine = ProbeEngine(DemoGateway(), ttl=1800, cooldown=900)
    sites = ["healthy.example", "no-robots.example", "banned.example"]

    # 1) 首轮预检：每个站点的检查项与结论
    for site in sites:
        print_result("首次预检", engine.probe(site))

    # 2) 第二轮：健康站命中缓存，不再触发任何检查
    print_result("再次预检（应命中缓存）", engine.probe("healthy.example"))

    # 3) 采集队列入口：不可采集站点直接拦在外面并给原因
    print("\n=== 队列拦截判定 ===")
    for site in sites:
        decision = engine.intercept(site)
        flag = "放行" if decision["allowed"] else "拦截"
        reason = f"，原因: {decision['reason']}" if decision["reason"] else ""
        print(f"  {flag} {site}（{decision['verdict']}）{reason}")

    # 4) 缓存快照与拦截记录
    print("\n=== 缓存快照 ===")
    for key, entry in engine.cache_info().items():
        print(f"  {key}: verdict={entry['verdict']} "
              f"cooldown={entry['cooldown']} 剩余有效期={entry['remaining']:.0f}s")

    print("\n=== 拦截记录 ===")
    for record in engine.intercepts():
        print(f"  {record['site']}: {record['reason']} "
              f"（冷却 {record['retry_after']}s 后可重试）")


if __name__ == "__main__":
    main()
