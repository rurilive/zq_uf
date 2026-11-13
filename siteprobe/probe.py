"""站点探测入口：兼容单页模式，支持多页并发的结构化报告。"""

import inspect
import time
from concurrent.futures import ThreadPoolExecutor

from .encoding import decode_body, garbled_ratio
from .parse import collect_anchors, extract_detail_links, infer_pagination
from .ratelimit import RateLimiter
from .report import REPORT_VERSION
from .samples import Samples
from .scoring import classify
from .signals import scan_signals


def probe(url, samples=None):
    """探测站点。

    probe(url, fetch_callable) 保持旧的单页行为；
    probe(url, Samples(...)) 或 probe(url, {...}) 返回完整结构化报告。
    """
    if callable(samples):
        return _legacy_probe(url, samples)
    if samples is None:
        samples = Samples()
    elif isinstance(samples, dict):
        samples = Samples(**samples)
    return _full_probe(url, samples)


def _legacy_probe(url, fetch):
    response = fetch(url) or {}
    body = response.get("body", "")
    headers = response.get("headers", {})
    return {
        "url": url,
        "status": response.get("status", 0),
        "bytes": len(body),
        "content_type": headers.get("content-type", ""),
    }


def _full_probe(url, samples):
    started_at = time.time()
    limiter = RateLimiter(samples.rate_per_sec)
    fetch_one = _make_fetcher(samples.fetcher, samples.timeout, limiter)

    list_urls = list(samples.list_urls) or [url]
    targets = [("home", url)] + [("list", item) for item in list_urls]
    with ThreadPoolExecutor(max_workers=samples.max_workers) as pool:
        responses = list(pool.map(lambda item: fetch_one(item[1]), targets))
    first_pass = {item_url: response for (_, item_url), response in zip(targets, responses)}

    list_payloads = []
    for item_url in list_urls:
        response = first_pass.get(item_url) or first_pass.get(url)
        list_payloads.append(_parse_page(item_url, response))

    pagination = infer_pagination(list_payloads)
    details = extract_detail_links(url, list_payloads, pagination, limit=samples.detail_count)

    detail_records = []
    if details["urls"]:
        with ThreadPoolExecutor(max_workers=samples.max_workers) as pool:
            detail_responses = list(pool.map(fetch_one, details["urls"]))
        detail_records = [
            _build_page_record(detail_url, detail_response, "detail")
            for detail_url, detail_response in zip(details["urls"], detail_responses)
        ]

    page_records = []
    for kind, item_url in targets:
        page_records.append(_build_page_record(item_url, first_pass[item_url], kind))
    page_records.extend(detail_records)

    list_anchor_map = {payload["url"]: payload["anchors"] for payload in list_payloads}
    for record in page_records:
        if record["url"] in list_anchor_map:
            record["anchors"] = [
                {"href": anchor.href, "text": anchor.text, "class": anchor.classes}
                for anchor in list_anchor_map[record["url"]]
            ]

    garbled_pages = [record["url"] for record in page_records if record["garbled_ratio"] >= 0.05]
    signals = scan_signals(url, page_records)
    assessment = classify(page_records, signals, pagination, garbled_pages)

    return {
        "report_version": REPORT_VERSION,
        "url": url,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "rate_limit": {"rate_per_sec": samples.rate_per_sec, "max_workers": samples.max_workers,
                        "requests": limiter.requests},
        "pagination": pagination,
        "details": details,
        "signals": signals,
        "assessment": assessment,
        "pages": page_records,
    }


def _make_fetcher(fetcher, timeout, limiter):
    accepts_timeout = _accepts_timeout(fetcher)

    def fetch_one(target_url):
        limiter.acquire()
        try:
            if accepts_timeout:
                response = fetcher(target_url, timeout=timeout)
            else:
                response = fetcher(target_url)
        except Exception as exc:  # fetcher 抛异常等同于请求失败，记录错误而不是中断探测
            response = {"status": 0, "body": b"", "headers": {}, "error": "fetcher_error: %s" % exc}
        return _normalize_response(target_url, response)

    return fetch_one


def _accepts_timeout(fetcher):
    try:
        signature = inspect.signature(fetcher)
        parameters = signature.parameters
        return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()) or any(
            name == "timeout" for name in parameters
        )
    except (TypeError, ValueError):
        return False


def _normalize_response(target_url, response):
    response = response or {}
    headers = response.get("headers") or {}
    body = response.get("body", b"")
    if isinstance(body, str):
        body = body.encode("utf-8", errors="replace")
    error = response.get("error", "") or ""
    timed_out = error == "timeout" or "timed out" in error.lower()
    return {
        "status": response.get("status", 0),
        "headers": headers,
        "body": body,
        "final_url": response.get("url") or target_url,
        "redirects": response.get("redirects") or [],
        "elapsed": round(float(response.get("elapsed") or 0.0), 4),
        "error": error,
        "timed_out": timed_out,
    }


def _parse_page(page_url, response):
    content_type = (response.get("headers") or {}).get("content-type", "")
    text, _, _ = decode_body(response.get("body", b""), content_type)
    anchors = collect_anchors(page_url, text)
    return {"url": page_url, "anchors": anchors, "text": text}


def _build_page_record(page_url, response, kind):
    headers = response.get("headers") or {}
    content_type = headers.get("content-type", "")
    text, charset, had_replacement = decode_body(response.get("body", b""), content_type)
    ratio = garbled_ratio(text)
    redirects = response.get("redirects") or []
    return {
        "url": page_url,
        "kind": kind,
        "text": text,
        "status": response.get("status", 0),
        "bytes": len(response.get("body", b"")),
        "content_type": content_type,
        "encoding": charset,
        "garbled_ratio": ratio,
        "garbled": had_replacement or ratio >= 0.05,
        "final_url": response.get("final_url", page_url),
        "redirect_count": len(redirects),
        "redirects": redirects,
        "elapsed": response.get("elapsed", 0.0),
        "timed_out": response.get("timed_out", False),
        "error": response.get("error", ""),
    }
