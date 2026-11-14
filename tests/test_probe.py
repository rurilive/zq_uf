"""crawlprobe 预检测试。

所有网络访问都由 FakeGateway 承接，测试过程不发出任何真实请求。
原有 ProbeTest 的断言保持不变；setUpModule 仅为模块级 probe/verdict
挂一个“一切正常”的离线网关。
"""

import os
import socket
import ssl
import sys
import unittest
from datetime import datetime, timedelta, timezone

# 保证 `python3 -m unittest discover -s tests` 与隔离模式 (-I) 下都能导入包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlprobe import (
    BLOCK,
    OK,
    WARN,
    CertInfo,
    HTTPResponse,
    ProbeEngine,
    gateway_scope,
    intercept_records,
    probe,
    reset_gateway,
    set_gateway,
    verdict,
)


# ----------------------------------------------------------------------
# 离线测试替身
# ----------------------------------------------------------------------
class FakeClock:
    """可手动推进的时钟，配合引擎的 clock 参数验证缓存过期。"""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_cert(days_left: float = 30.0) -> CertInfo:
    now = datetime.now(timezone.utc)
    return CertInfo(
        subject="good.test",
        issuer="Test CA",
        not_before=now - timedelta(days=30),
        not_after=now + timedelta(days=days_left),
    )


def make_response(url: str, status: int = 200, body: bytes = b"",
                  headers: dict | None = None) -> HTTPResponse:
    return HTTPResponse(url=url, status=status,
                        headers={k.lower(): v for k, v in (headers or {}).items()},
                        body=body)


class FakeGateway:
    """按脚本返回 DNS/TLS/HTTP 结果的假网关；任何未配置的访问都会报错。"""

    def __init__(self, *, resolve_result=None, resolve_error=None,
                 cert=None, tls_error=None, routes=None):
        self._resolve_result = resolve_result or ["198.51.100.7"]
        self._resolve_error = resolve_error
        self._cert = cert if cert is not None else make_cert()
        self._tls_error = tls_error
        self._routes = routes or {}
        self.calls = []

    def resolve(self, host):
        self.calls.append(("resolve", host))
        if self._resolve_error is not None:
            raise self._resolve_error
        return list(self._resolve_result)

    def tls_probe(self, host, port=443):
        self.calls.append(("tls", (host, port)))
        if self._tls_error is not None:
            raise self._tls_error
        return self._cert

    def http_get(self, url):
        self.calls.append(("get", url))
        if url not in self._routes:
            raise OSError(f"FakeGateway 未配置路由: {url}")
        result = self._routes[url]
        if isinstance(result, Exception):
            raise result
        return result


ROBOTS_URL = "https://shop/robots.txt"
HOME_URL = "https://shop/"


def ok_routes(robots_body="User-agent: *\nAllow: /\n",
              home_body=b"<html>welcome</html>"):
    return {
        ROBOTS_URL: make_response(ROBOTS_URL, 200, robots_body.encode()),
        HOME_URL: make_response(HOME_URL, 200, home_body),
    }


def build_ok_gateway() -> FakeGateway:
    return FakeGateway(routes=ok_routes())


def build_engine(gateway, *, ttl=1800.0, cooldown=900.0, clock=None):
    return ProbeEngine(gateway, ttl=ttl, cooldown=cooldown,
                       clock=clock or FakeClock())


def check_named(result, name):
    matches = [c for c in result["checks"] if c["check"] == name]
    assert matches, f"检查项 {name} 不存在: {result['checks']}"
    return matches[0]


def setUpModule():
    # 模块级 probe/verdict 默认走离线网关，保证旧用例无网络依赖。
    set_gateway(build_ok_gateway())


def tearDownModule():
    reset_gateway()


# ----------------------------------------------------------------------
# 原有测试（断言未改）
# ----------------------------------------------------------------------
class ProbeTest(unittest.TestCase):
    def test_probe_ok(self):
        self.assertTrue(probe("shop")["ok"])

    def test_probe_echoes_site(self):
        self.assertEqual(probe("shop")["site"], "shop")

    def test_verdict_ok(self):
        self.assertEqual(verdict("shop"), "ok")


# ----------------------------------------------------------------------
# DNS
# ----------------------------------------------------------------------
class DNSTest(unittest.TestCase):
    def test_dns_failure_blocks_site(self):
        gateway = FakeGateway(
            resolve_error=socket.gaierror(-3, "Temporary failure in name resolution"),
            routes=ok_routes(),
        )
        engine = build_engine(gateway)

        result = engine.probe("shop")

        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], BLOCK)
        dns = check_named(result, "dns")
        self.assertFalse(dns["ok"])
        self.assertEqual(dns["severity"], BLOCK)
        self.assertIn("DNS", dns["detail"])
        self.assertTrue(dns["error"])  # 异常路径必须带明确错误
        # DNS 不通时后续检查没有意义，不再发起任何请求
        self.assertEqual([c["check"] for c in result["checks"]], ["dns"])
        self.assertFalse(any(kind == "get" for kind, _ in gateway.calls))

        decision = engine.intercept("shop")
        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["reason"])


# ----------------------------------------------------------------------
# TLS 证书
# ----------------------------------------------------------------------
class TLSTest(unittest.TestCase):
    def test_expired_certificate_blocks(self):
        gateway = FakeGateway(cert=make_cert(days_left=-2), routes=ok_routes())
        result = build_engine(gateway).probe("shop")

        self.assertEqual(result["verdict"], BLOCK)
        tls = check_named(result, "tls")
        self.assertFalse(tls["ok"])
        self.assertEqual(tls["severity"], BLOCK)
        self.assertIn("过期", tls["detail"])

    def test_tls_handshake_error_blocks_with_error(self):
        gateway = FakeGateway(
            tls_error=ssl.SSLError("certificate verify failed: self-signed certificate"),
            routes=ok_routes(),
        )
        result = build_engine(gateway).probe("shop")

        self.assertEqual(result["verdict"], BLOCK)
        tls = check_named(result, "tls")
        self.assertEqual(tls["severity"], BLOCK)
        self.assertIn("SSLError", tls["error"])

    def test_certificate_expiring_soon_warns(self):
        gateway = FakeGateway(cert=make_cert(days_left=3), routes=ok_routes())
        result = build_engine(gateway).probe("shop")

        self.assertEqual(result["verdict"], WARN)
        self.assertTrue(result["ok"])  # warn 不拦截
        self.assertEqual(check_named(result, "tls")["severity"], WARN)

    def test_http_site_skips_tls(self):
        gateway = FakeGateway(routes={
            "http://shop/robots.txt": make_response(
                "http://shop/robots.txt", 200, b"User-agent: *\nAllow: /\n"),
            "http://shop/": make_response("http://shop/", 200, b"ok"),
        })
        result = build_engine(gateway).probe("http://shop")
        self.assertEqual(result["verdict"], OK)
        self.assertFalse(any(c["check"] == "tls" for c in result["checks"]))


# ----------------------------------------------------------------------
# robots
# ----------------------------------------------------------------------
class RobotsTest(unittest.TestCase):
    def test_robots_disallow_blocks(self):
        gateway = FakeGateway(routes=ok_routes(
            robots_body="User-agent: *\nDisallow: /\n"))
        engine = build_engine(gateway)

        result = engine.probe("shop")
        self.assertEqual(result["verdict"], BLOCK)
        robots = check_named(result, "robots")
        self.assertFalse(robots["ok"])
        self.assertEqual(robots["severity"], BLOCK)

        decision = engine.intercept("shop")
        self.assertFalse(decision["allowed"])
        self.assertIn("robots", decision["reason"])

    def test_robots_forbidden_status_blocks(self):
        routes = ok_routes()
        routes[ROBOTS_URL] = make_response(ROBOTS_URL, 403, b"forbidden")
        result = build_engine(FakeGateway(routes=routes)).probe("shop")
        self.assertEqual(check_named(result, "robots")["severity"], BLOCK)

    def test_robots_missing_allows(self):
        routes = ok_routes()
        routes[ROBOTS_URL] = make_response(ROBOTS_URL, 404, b"not found")
        result = build_engine(FakeGateway(routes=routes)).probe("shop")
        self.assertEqual(result["verdict"], OK)
        self.assertEqual(check_named(result, "robots")["severity"], OK)

    def test_robots_fetch_error_blocks_with_error(self):
        routes = ok_routes()
        routes[ROBOTS_URL] = OSError("connection refused")
        result = build_engine(FakeGateway(routes=routes)).probe("shop")
        robots = check_named(result, "robots")
        self.assertEqual(robots["severity"], BLOCK)
        self.assertIn("OSError", robots["error"])


# ----------------------------------------------------------------------
# 封禁特征
# ----------------------------------------------------------------------
class BanSignatureTest(unittest.TestCase):
    def test_body_ban_keyword_blocks(self):
        gateway = FakeGateway(routes=ok_routes(
            home_body=b"<html>Access Denied. Your IP has been blocked.</html>"))
        result = build_engine(gateway).probe("shop")

        self.assertEqual(result["verdict"], BLOCK)
        ban = check_named(result, "ban")
        self.assertFalse(ban["ok"])
        self.assertTrue(ban["signatures"])

    def test_ban_header_blocks(self):
        routes = ok_routes()
        routes[HOME_URL] = make_response(
            HOME_URL, 403, b"blocked", headers={"X-WAF": "blocked"})
        result = build_engine(FakeGateway(routes=routes)).probe("shop")
        self.assertEqual(check_named(result, "ban")["severity"], BLOCK)

    def test_rate_limit_status_blocks(self):
        routes = ok_routes()
        routes[HOME_URL] = make_response(
            HOME_URL, 429, b"too many requests", headers={"Retry-After": "60"})
        result = build_engine(FakeGateway(routes=routes)).probe("shop")
        self.assertEqual(check_named(result, "ban")["severity"], BLOCK)

    def test_waf_403_blocks(self):
        routes = ok_routes()
        routes[HOME_URL] = make_response(
            HOME_URL, 403, b"nope", headers={"Server": "cloudflare"})
        result = build_engine(FakeGateway(routes=routes)).probe("shop")
        self.assertEqual(check_named(result, "ban")["severity"], BLOCK)

    def test_plain_403_is_warn_not_ban(self):
        # 普通 403（无 WAF 特征）算首页异常，不能误判封禁
        routes = ok_routes()
        routes[HOME_URL] = make_response(
            HOME_URL, 403, b"forbidden", headers={"Server": "nginx"})
        result = build_engine(FakeGateway(routes=routes)).probe("shop")
        self.assertEqual(check_named(result, "ban")["severity"], OK)
        self.assertEqual(check_named(result, "homepage")["severity"], WARN)
        self.assertEqual(result["verdict"], WARN)

    def test_ban_recorded_in_intercepts(self):
        gateway = FakeGateway(routes=ok_routes(
            home_body="访问被拒绝，人机验证".encode()))
        engine = build_engine(gateway)
        engine.probe("shop")
        records = engine.intercepts()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["site"], "shop")
        self.assertIn("ban", records[0]["checks"])
        self.assertTrue(records[0]["reason"])


# ----------------------------------------------------------------------
# 缓存、过期与冷却重试
# ----------------------------------------------------------------------
class CacheTest(unittest.TestCase):
    def test_second_probe_is_served_from_cache(self):
        gateway = build_ok_gateway()
        engine = build_engine(gateway)

        first = engine.probe("shop")
        second = engine.probe("shop")

        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertGreater(second["cache_remaining"], 0)
        resolve_calls = [c for c in gateway.calls if c[0] == "resolve"]
        self.assertEqual(len(resolve_calls), 1)  # 缓存命中不再访问网关

    def test_cache_expiry_triggers_reprobe(self):
        clock = FakeClock()
        gateway = build_ok_gateway()
        engine = build_engine(gateway, ttl=10.0, clock=clock)

        engine.probe("shop")
        clock.advance(10.0 + 1)  # 超过 TTL，缓存过期
        refreshed = engine.probe("shop")

        self.assertFalse(refreshed["cached"])
        self.assertEqual(
            len([c for c in gateway.calls if c[0] == "resolve"]), 2)

    def test_blocked_site_cools_down_then_retries(self):
        clock = FakeClock()
        gateway = FakeGateway(
            resolve_error=socket.gaierror(-3, "name resolution failed"))
        engine = build_engine(gateway, cooldown=30.0, clock=clock)

        first = engine.probe("shop")
        self.assertEqual(first["verdict"], BLOCK)

        # 冷却期内：直接返回缓存的 block，不重试 DNS
        clock.advance(10.0)
        cooled = engine.probe("shop")
        self.assertTrue(cooled["cached"])
        self.assertTrue(cooled["cooldown"])
        self.assertEqual(
            len([c for c in gateway.calls if c[0] == "resolve"]), 1)

        # 冷却结束：重新预检，此时 DNS 恢复，站点放行
        clock.advance(21.0)
        gateway._resolve_error = None
        gateway._routes = ok_routes()
        retried = engine.probe("shop")
        self.assertFalse(retried["cached"])
        self.assertEqual(retried["verdict"], OK)
        self.assertEqual(
            len([c for c in gateway.calls if c[0] == "resolve"]), 2)

    def test_cache_info_reports_remaining_and_cooldown(self):
        clock = FakeClock()
        gateway = FakeGateway(
            resolve_error=socket.gaierror(-3, "name resolution failed"))
        engine = build_engine(gateway, ttl=100.0, cooldown=40.0, clock=clock)
        engine.probe("shop")
        clock.advance(10.0)

        info = engine.cache_info()
        entry = info["https://shop"]
        self.assertTrue(entry["cooldown"])
        self.assertEqual(entry["verdict"], BLOCK)
        self.assertEqual(entry["remaining"], 30.0)

    def test_robots_block_cools_down_then_recovers(self):
        clock = FakeClock()
        gateway = FakeGateway(routes=ok_routes(
            robots_body="User-agent: *\nDisallow: /\n"))
        engine = build_engine(gateway, cooldown=20.0, clock=clock)

        self.assertEqual(engine.probe("shop")["verdict"], BLOCK)
        clock.advance(20.0 + 1)  # 冷却结束
        # 站点恢复：robots 放开
        gateway._routes[ROBOTS_URL] = make_response(
            ROBOTS_URL, 200, b"User-agent: *\nAllow: /\n")
        retried = engine.probe("shop")
        self.assertEqual(retried["verdict"], OK)
        self.assertFalse(retried["cached"])

    def test_force_probe_bypasses_cache(self):
        gateway = build_ok_gateway()
        engine = build_engine(gateway)
        engine.probe("shop")
        forced = engine.probe("shop", force=True)
        self.assertFalse(forced["cached"])
        self.assertEqual(
            len([c for c in gateway.calls if c[0] == "resolve"]), 2)


# ----------------------------------------------------------------------
# 队列拦截与异常路径
# ----------------------------------------------------------------------
class InterceptTest(unittest.TestCase):
    def test_ok_site_allowed_into_queue(self):
        decision = build_engine(build_ok_gateway()).intercept("shop")
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["verdict"], OK)

    def test_blocked_site_kept_out_of_queue_with_reason(self):
        gateway = FakeGateway(routes=ok_routes(
            robots_body="User-agent: *\nDisallow: /\n"))
        engine = build_engine(gateway)
        decision = engine.intercept("shop")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["verdict"], BLOCK)
        self.assertTrue(decision["reason"])
        self.assertEqual(len(engine.intercepts()), 1)

    def test_module_level_intercept_records(self):
        import crawlprobe

        gateway = FakeGateway(
            resolve_error=socket.gaierror(-3, "name resolution failed"))
        with gateway_scope(gateway):
            decision = crawlprobe.intercept("shop")
        self.assertFalse(decision["allowed"])
        self.assertTrue(intercept_records(gateway))

    def test_invalid_site_raises_value_error(self):
        engine = build_engine(build_ok_gateway())
        with self.assertRaises(ValueError):
            engine.probe("")
        with self.assertRaises(ValueError):
            engine.probe("ftp://shop/path")
        with self.assertRaises(ValueError):
            engine.probe("   ")

    def test_homepage_fetch_error_is_warn_with_explicit_error(self):
        routes = ok_routes()
        routes[HOME_URL] = OSError("network unreachable")
        result = build_engine(FakeGateway(routes=routes)).probe("shop")
        homepage = check_named(result, "homepage")
        self.assertEqual(homepage["severity"], WARN)
        self.assertIn("OSError", homepage["error"])
        ban = check_named(result, "ban")
        self.assertEqual(ban["severity"], WARN)
        self.assertEqual(result["verdict"], WARN)  # 不拦截，但风险可见

    def test_gateway_scope_restores_default(self):
        probe_mod = sys.modules["crawlprobe.probe"]

        previous = probe_mod._default_gateway
        with gateway_scope(build_ok_gateway()):
            self.assertEqual(verdict("shop"), OK)
        self.assertIs(probe_mod._default_gateway, previous)


if __name__ == "__main__":
    unittest.main()
