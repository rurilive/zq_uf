"""站点采集前预检。

检查项（按执行顺序）：

1. ``dns``     DNS 解析，失败直接判定不可采集；
2. ``tls``     HTTPS 站点的证书链与有效期；
3. ``robots``  robots.txt 禁止规则；
4. ``homepage`` 首页 HTTP 状态；
5. ``ban``     封禁特征（HTTP 头、状态码、正文关键词）。

结论口径见 :data:`SEVERITY_RANK` 与 README：``block`` 站点在采集队列外拦截，
``warn`` 可以采集但带风险提示，``ok`` 正常入队。

对外保持两个模块级函数：:func:`probe` 与 :func:`verdict`，签名与旧版一致；
需要注入 I/O 层（测试、离线演示）时传 ``gateway`` 参数，或用
:func:`gateway_scope` / :func:`set_gateway` 切换默认网关。
"""

from __future__ import annotations

import re
import time
import urllib.robotparser
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .gateway import USER_AGENT, CertInfo, HTTPResponse, StdlibGateway

# 结论：严重程度从高到低。block 表示禁止采集，warn 可采集但有风险，ok 正常。
OK = "ok"
WARN = "warn"
BLOCK = "block"
SEVERITY_RANK = {OK: 0, WARN: 1, BLOCK: 2}

# 检查名
CHECK_DNS = "dns"
CHECK_TLS = "tls"
CHECK_ROBOTS = "robots"
CHECK_HOMEPAGE = "homepage"
CHECK_BAN = "ban"

# 默认时间策略（秒）
DEFAULT_TTL = 1800              # 正常预检结果缓存 30 分钟
DEFAULT_COOLDOWN = 900          # 不可采集站点冷却 15 分钟后才重试
TLS_EXPIRY_WARN_DAYS = 7        # 证书 7 天内到期给 warn

# robots.txt 无法确认允许性时视为禁止的状态码
_ROBOTS_BLOCK_STATUSES = frozenset({401, 403, 407})

# 封禁特征：HTTP 头
_BAN_HEADERS = {
    "x-waf": ("blocked", "reject", "deny", "1"),
    "x-block": ("1", "true", "block"),
    "cf-mitigated": ("challenge",),
}
# 封禁特征：状态码 + 头组合
_BAN_STATUS_WITH_HEADER = {
    403: ("server",),          # 403 本身常见，要求带明显代理/WAF 头才算封禁
    429: (),                   # 429 限流即封禁特征
    503: ("retry-after",),     # 503 + Retry-After 是临时封禁
}
# 封禁特征：正文关键词（命中即判定）
_BAN_BODY_PATTERNS = re.compile(
    r"(access\s*denied|访问被拒绝|您的访问被封禁|ip\s*has\s*been\s*blocked|"
    r"blocked\s*by\s*(cloudflare|waf|anti[- ]?bot)|captcha\s*verification|"
    r"爬虫|captcha|人机验证)",
    re.IGNORECASE,
)
# 403 时说明是 WAF/代理的 Server 头片段
_WAF_SERVER_HINTS = re.compile(
    r"(cloudflare|sucuri|akamai|incapsula|waf|f5|bigip|barracuda|安全|防火墙)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedSite:
    """站点归一化结果。"""

    raw: str
    scheme: str
    host: str
    port: int | None

    @property
    def base_url(self) -> str:
        netloc = self.host
        if self.port is not None and not (
            (self.scheme == "https" and self.port == 443)
            or (self.scheme == "http" and self.port == 80)
        ):
            netloc = f"{self.host}:{self.port}"
        return urlunsplit((self.scheme, netloc, "", "", ""))

    @property
    def key(self) -> str:
        """缓存键：归一化后的基础 URL。"""
        return self.base_url.lower()


@dataclass
class CacheEntry:
    """一条站点预检缓存。"""

    result: dict[str, Any]
    expires_at: float
    cooldown: bool
    created_at: float


def _check(name: str, ok: bool, severity: str, detail: str,
           error: str | None = None, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "check": name,
        "ok": ok,
        "severity": severity,
        "detail": detail,
    }
    if error is not None:
        entry["error"] = error
    entry.update(extra)
    return entry


def normalize_site(site: str) -> ParsedSite:
    """把用户输入归一化成 scheme/host/port。

    接受 ``example.com``、``https://example.com``、``http://a:8080/`` 等形式。
    非法输入（空串、无主机名、非 http(s) 协议）抛 :class:`ValueError`，
    调用方拿到的是明确的参数错误而不是网络异常。
    """
    if not isinstance(site, str) or not site.strip():
        raise ValueError("site 必须是非空字符串，例如 'example.com'")
    raw = site.strip()
    candidate = raw if "://" in raw else f"https://{raw}"
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"site 只支持 http/https 协议: {site!r}")
    host = parts.hostname
    if not host:
        raise ValueError(f"site 缺少主机名: {site!r}")
    return ParsedSite(raw=raw, scheme=parts.scheme, host=host, port=parts.port)


class ProbeEngine:
    """预检引擎：检查、结论、缓存、冷却与拦截记录。

    ``gateway`` 是注入的 I/O 层，只需实现 ``resolve`` / ``tls_probe`` /
    ``http_get`` 三个方法（见 :class:`~crawlprobe.gateway.StdlibGateway`）。
    ``clock`` 返回单调时间戳，测试可注入假时钟验证缓存过期。
    """

    def __init__(
        self,
        gateway: Any | None = None,
        *,
        ttl: float = DEFAULT_TTL,
        cooldown: float = DEFAULT_COOLDOWN,
        user_agent: str = USER_AGENT,
        clock: Any = time.monotonic,
    ):
        self.gateway = gateway or StdlibGateway(user_agent=user_agent)
        self.ttl = ttl
        self.cooldown = cooldown
        self.user_agent = user_agent
        self._clock = clock
        self._cache: dict[str, CacheEntry] = {}
        self._intercepts: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 对外方法
    # ------------------------------------------------------------------
    def probe(self, site: str, *, force: bool = False) -> dict[str, Any]:
        """对单个站点执行预检，返回完整预检结果。"""
        parsed = normalize_site(site)
        now = self._clock()
        if not force:
            cached = self._cache.get(parsed.key)
            if cached is not None and now < cached.expires_at:
                return self._from_cache(parsed, cached, now)
            if cached is not None and now >= cached.expires_at:
                # 过期缓存不再使用，清掉后重新预检（冷却站点同样在此重试）。
                self._cache.pop(parsed.key, None)

        checks = self._run_checks(parsed)
        severity, reasons = self._assemble(checks)
        result = {
            "site": parsed.raw,
            "ok": severity != BLOCK,
            "verdict": severity,
            "checks": checks,
            "reasons": reasons,
            "cached": False,
            "cache_ttl": self.cooldown if severity == BLOCK else self.ttl,
        }
        entry = CacheEntry(
            result=result,
            expires_at=now + result["cache_ttl"],
            cooldown=severity == BLOCK,
            created_at=now,
        )
        self._cache[parsed.key] = entry
        if severity == BLOCK:
            self._intercepts.append(
                {
                    "site": parsed.raw,
                    "reason": "; ".join(reasons),
                    "checks": [c["check"] for c in checks if c["severity"] == BLOCK],
                    "cooldown_until": entry.expires_at,
                    "retry_after": self.cooldown,
                }
            )
        return dict(result)

    def verdict(self, site: str) -> str:
        """只取结论：``ok`` / ``warn`` / ``block``。"""
        return self.probe(site)["verdict"]

    def intercept(self, site: str) -> dict[str, Any]:
        """采集队列入口：返回是否放行及原因，不可采集站点直接拦在外面。"""
        result = self.probe(site)
        if result["verdict"] == BLOCK:
            return {
                "site": result["site"],
                "allowed": False,
                "verdict": BLOCK,
                "reason": "; ".join(result["reasons"]),
            }
        return {
            "site": result["site"],
            "allowed": True,
            "verdict": result["verdict"],
            "reason": "" if result["verdict"] == OK else "; ".join(result["reasons"]),
        }

    def cache_info(self) -> dict[str, Any]:
        """当前缓存快照（含剩余有效期与冷却标记），用于输出/排障。"""
        now = self._clock()
        return {
            key: {
                "site": entry.result["site"],
                "verdict": entry.result["verdict"],
                "cooldown": entry.cooldown,
                "remaining": max(0.0, entry.expires_at - now),
                "expires_at": entry.expires_at,
            }
            for key, entry in self._cache.items()
        }

    def clear_cache(self) -> None:
        """清空预检缓存。"""
        self._cache.clear()

    def intercepts(self) -> list[dict[str, Any]]:
        """拦截记录（只记录不可采集站点）。"""
        return [dict(item) for item in self._intercepts]

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _from_cache(self, parsed: ParsedSite, entry: CacheEntry, now: float) -> dict[str, Any]:
        result = dict(entry.result)
        result["cached"] = True
        result["cache_remaining"] = max(0.0, entry.expires_at - now)
        result["cooldown"] = entry.cooldown
        return result

    def _run_checks(self, parsed: ParsedSite) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []

        # 1) DNS —— 解析不了就没有后续检查的意义
        try:
            addresses = self.gateway.resolve(parsed.host)
            checks.append(
                _check(
                    CHECK_DNS,
                    True,
                    OK,
                    f"解析到 {len(addresses)} 个地址: {', '.join(addresses)}",
                    addresses=addresses,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 任何解析失败都要落成明确检查项
            checks.append(
                _check(
                    CHECK_DNS,
                    False,
                    BLOCK,
                    "DNS 解析失败，站点无法访问",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            return checks

        # 2) TLS —— 仅 HTTPS
        if parsed.scheme == "https":
            checks.append(self._check_tls(parsed))

        # 3) robots.txt
        checks.append(self._check_robots(parsed))

        # 4) 首页
        home_check, home_response = self._check_homepage(parsed)
        checks.append(home_check)

        # 5) 封禁特征（依赖首页响应；首页请求本身失败时跳过）
        checks.append(self._check_ban(home_response))
        return checks

    def _check_tls(self, parsed: ParsedSite) -> dict[str, Any]:
        port = parsed.port or 443
        try:
            cert: CertInfo = self.gateway.tls_probe(parsed.host, port)
        except Exception as exc:  # noqa: BLE001
            return _check(
                CHECK_TLS,
                False,
                BLOCK,
                "TLS 握手或证书校验失败",
                error=f"{type(exc).__name__}: {exc}",
            )
        now = time.time()
        if cert.not_after is not None and cert.not_after.timestamp() < now:
            return _check(
                CHECK_TLS,
                False,
                BLOCK,
                f"证书已过期（到期时间 {cert.not_after.isoformat()}，"
                f"签发主体 {cert.subject or '未知'}）",
                not_after=cert.not_after.isoformat(),
            )
        if cert.not_after is not None:
            days_left = (cert.not_after.timestamp() - now) / 86400
            if days_left <= TLS_EXPIRY_WARN_DAYS:
                return _check(
                    CHECK_TLS,
                    True,
                    WARN,
                    f"证书将在 {days_left:.1f} 天内到期（{cert.not_after.isoformat()}）",
                    not_after=cert.not_after.isoformat(),
                )
        return _check(
            CHECK_TLS,
            True,
            OK,
            f"证书有效，到期时间 {cert.not_after.isoformat() if cert.not_after else '未知'}",
            not_after=cert.not_after.isoformat() if cert.not_after else None,
        )

    def _check_robots(self, parsed: ParsedSite) -> dict[str, Any]:
        robots_url = f"{parsed.base_url}/robots.txt"
        try:
            response: HTTPResponse = self.gateway.http_get(robots_url)
        except Exception as exc:  # noqa: BLE001
            # robots 拿不到属于“无法确认”，保守地禁止采集并带明确错误
            return _check(
                CHECK_ROBOTS,
                False,
                BLOCK,
                "robots.txt 无法获取，无法确认采集许可",
                error=f"{type(exc).__name__}: {exc}",
            )
        if response.status >= 400:
            if response.status in _ROBOTS_BLOCK_STATUSES:
                return _check(
                    CHECK_ROBOTS,
                    False,
                    BLOCK,
                    f"robots.txt 返回 {response.status}，站点拒绝爬虫访问",
                    status=response.status,
                )
            # 404/410/5xx：没有可用规则，按惯例视为未限制，但留下提示
            return _check(
                CHECK_ROBOTS,
                True,
                WARN if response.status >= 500 else OK,
                f"robots.txt 返回 {response.status}，按无限制规则处理",
                status=response.status,
            )
        parser = urllib.robotparser.RobotFileParser()
        home_path = urlsplit(parsed.base_url).path or "/"
        try:
            parser.parse(response.text().splitlines())
            allowed = parser.can_fetch(self.user_agent, f"{parsed.base_url}/")
        except Exception as exc:  # noqa: BLE001 - 规则文件畸形时给明确错误
            return _check(
                CHECK_ROBOTS,
                False,
                BLOCK,
                "robots.txt 规则无法解析",
                error=f"{type(exc).__name__}: {exc}",
            )
        if not allowed:
            delay = parser.crawl_delay(self.user_agent)
            return _check(
                CHECK_ROBOTS,
                False,
                BLOCK,
                f"robots.txt 禁止 {self.user_agent} 抓取首页路径 {home_path}",
                crawl_delay=delay,
            )
        return _check(
            CHECK_ROBOTS,
            True,
            OK,
            "robots.txt 允许抓取",
            crawl_delay=parser.crawl_delay(self.user_agent),
        )

    def _check_homepage(self, parsed: ParsedSite) -> tuple[dict[str, Any], HTTPResponse | None]:
        """返回 (检查项, 原始响应)；原始响应供封禁特征检查复用。"""
        try:
            response = self.gateway.http_get(parsed.base_url + "/")
        except Exception as exc:  # noqa: BLE001
            return (
                _check(
                    CHECK_HOMEPAGE,
                    False,
                    WARN,
                    "首页请求失败，站点可能不稳定",
                    error=f"{type(exc).__name__}: {exc}",
                ),
                None,
            )
        if 200 <= response.status < 300:
            return (
                _check(
                    CHECK_HOMEPAGE,
                    True,
                    OK,
                    f"首页可访问，状态码 {response.status}",
                    status=response.status,
                    final_url=response.url,
                ),
                response,
            )
        if 300 <= response.status < 400:
            return (
                _check(
                    CHECK_HOMEPAGE,
                    True,
                    WARN,
                    f"首页返回重定向 {response.status}，采集需跟随跳转",
                    status=response.status,
                    final_url=response.url,
                ),
                response,
            )
        return (
            _check(
                CHECK_HOMEPAGE,
                False,
                WARN,
                f"首页状态码异常: {response.status}",
                status=response.status,
                final_url=response.url,
            ),
            response,
        )

    def _check_ban(self, response: HTTPResponse | None) -> dict[str, Any]:
        if response is None:
            return _check(
                CHECK_BAN,
                False,
                WARN,
                "首页不可用，封禁特征无法识别",
                error="首页请求失败，无响应可检查",
            )
        hits: list[str] = []
        headers = response.headers

        for header, bad_values in _BAN_HEADERS.items():
            value = headers.get(header)
            if value is not None and value.strip().lower() in bad_values:
                hits.append(f"响应头 {header}: {value.strip()}")

        status = response.status
        if status in _BAN_STATUS_WITH_HEADER:
            required = _BAN_STATUS_WITH_HEADER[status]
            if not required:
                hits.append(f"状态码 {status}（限流/封禁）")
            else:
                for header in required:
                    value = headers.get(header)
                    if value is None:
                        continue
                    if status == 403 and not _WAF_SERVER_HINTS.search(value):
                        continue
                    hits.append(f"状态码 {status} 且响应头 {header}: {value.strip()}")

        match = _BAN_BODY_PATTERNS.search(response.text())
        if match:
            hits.append(f"正文命中封禁关键词: {match.group(0)!r}")

        if hits:
            return _check(
                CHECK_BAN,
                False,
                BLOCK,
                "识别到封禁特征，继续采集会导致整批任务被封",
                signatures=hits,
            )
        return _check(CHECK_BAN, True, OK, "未发现封禁特征")

    @staticmethod
    def _assemble(checks: list[dict[str, Any]]) -> tuple[str, list[str]]:
        severity = OK
        for check in checks:
            if SEVERITY_RANK[check["severity"]] > SEVERITY_RANK[severity]:
                severity = check["severity"]
        reasons = [
            f"[{check['check']}] {check['detail']}"
            for check in checks
            if check["severity"] != OK
        ]
        return severity, reasons


# ----------------------------------------------------------------------
# 模块级门面：保持旧接口 probe(site) / verdict(site)
# ----------------------------------------------------------------------
_default_gateway: Any = None
_gateway_engines: "weakref.WeakKeyDictionary[Any, ProbeEngine]" = weakref.WeakKeyDictionary()
_default_engine = ProbeEngine(StdlibGateway())


def _engine_for(gateway: Any | None) -> ProbeEngine:
    if gateway is None:
        if _default_gateway is None:
            return _default_engine
        gateway = _default_gateway
    engine = _gateway_engines.get(gateway)
    if engine is None:
        engine = ProbeEngine(gateway)
        _gateway_engines[gateway] = engine
    return engine


def set_gateway(gateway: Any) -> None:
    """切换模块级默认 I/O 网关（测试/离线演示用）。"""
    global _default_gateway
    _default_gateway = gateway


def reset_gateway() -> None:
    """恢复模块级默认网关为真实标准库实现。"""
    global _default_gateway
    _default_gateway = None


@contextmanager
def gateway_scope(gateway: Any):
    """临时切换模块级默认网关，退出时恢复。"""
    global _default_gateway
    previous = _default_gateway
    _default_gateway = gateway
    try:
        yield
    finally:
        _default_gateway = previous


def probe(site, gateway: Any | None = None) -> dict[str, Any]:
    """站点预检，返回检查项、结论、缓存与原因（签名兼容旧版）。"""
    return _engine_for(gateway).probe(site)


def verdict(site, gateway: Any | None = None) -> str:
    """站点结论：``ok`` / ``warn`` / ``block``（签名兼容旧版）。"""
    return _engine_for(gateway).verdict(site)


def intercept(site, gateway: Any | None = None) -> dict[str, Any]:
    """队列入口：不可采集的站点直接拦截并给出原因。"""
    return _engine_for(gateway).intercept(site)


def cache_info(gateway: Any | None = None) -> dict[str, Any]:
    """模块级缓存快照。"""
    return _engine_for(gateway).cache_info()


def clear_cache(gateway: Any | None = None) -> None:
    """清空模块级缓存。"""
    _engine_for(gateway).clear_cache()


def intercept_records(gateway: Any | None = None) -> list[dict[str, Any]]:
    """模块级拦截记录。"""
    return _engine_for(gateway).intercepts()
