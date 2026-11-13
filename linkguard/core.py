"""跳转处理：规范化判环、会话参数归并、跨域策略、次数上限。"""

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

SESSION_PARAMS = frozenset({
    "sid", "sessid", "sessionid", "session_id",
    "jsessionid", "phpsessid", "aspsessionid", "asp.net_sessionid",
})

DEFAULT_MAX_REDIRECTS = 10


def normalize(url):
    """规范化 URL：小写 scheme/host、去默认端口、去会话参数、排序查询、去 fragment。"""
    parts = urlsplit(url)
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and not (scheme == "http" and port == 80 or scheme == "https" and port == 443):
        host = "%s:%d" % (host, port)
    path = parts.path or "/"
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in SESSION_PARAMS
    )
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def _is_redirect(response):
    return (
        isinstance(response, dict)
        and response.get("status") in REDIRECT_STATUSES
        and response.get("location")
    )


def _is_cross_domain(current, target):
    return (urlsplit(current).hostname or "").lower() != (
        urlsplit(target).hostname or ""
    ).lower()


def _report(final_url, redirects, cycle, error, chain, cross_domain, response):
    return {
        "final_url": final_url,
        "redirects": redirects,
        "cycle": cycle,
        "error": error,
        "chain": chain,
        "cross_domain": cross_domain,
        "response": response,
    }


def follow(url, fetch, max_redirects=DEFAULT_MAX_REDIRECTS, follow_cross_domain=False):
    """跟随跳转链，返回最终响应或跳转报告。

    无跳转时原样返回 fetch 的响应；发生跳转时返回报告 dict，
    含 final_url、redirects、cycle、error、chain、cross_domain、response。
    """
    visited = {normalize(url)}
    chain = [url]
    cross_domain = []
    redirects = 0
    current = url
    while True:
        response = fetch(current)
        if not _is_redirect(response):
            if redirects == 0 and not cross_domain:
                return response
            return _report(current, redirects, False, None, chain, cross_domain, response)
        target = urljoin(current, response["location"])
        if normalize(target) in visited:
            chain.append(target)
            return _report(current, redirects, True, "redirect cycle detected",
                           chain, cross_domain, response)
        if redirects >= max_redirects:
            return _report(current, redirects, False,
                           "max redirects exceeded (%d)" % max_redirects,
                           chain, cross_domain, response)
        if _is_cross_domain(current, target):
            cross_domain.append({"from": current, "to": target})
            if not follow_cross_domain:
                return _report(current, redirects, False,
                               "cross-domain redirect not followed",
                               chain, cross_domain, response)
        visited.add(normalize(target))
        chain.append(target)
        redirects += 1
        current = target
