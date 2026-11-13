"""可续传、可校验、按站点限流的附件下载。

流程：探测服务端是否支持 Range -> 依据 ``.part`` 临时文件续传或重下 ->
校验 Content-Length 与可选校验和 -> 原子替换为最终文件。
"""

import contextlib
import hashlib
import inspect
import io
import logging
import os
import threading
from urllib import request as urlrequest
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

PART_SUFFIX = ".part"
DEFAULT_PER_HOST = 2
CHUNK_SIZE = 64 * 1024


class VerificationError(Exception):
    """下载内容长度或校验和与预期不一致，临时文件已保留。"""


class SiteLimiter:
    """按主机名限制并发下载数，不同站点互不影响。"""

    def __init__(self, per_host=DEFAULT_PER_HOST):
        self._per_host = per_host
        self._guard = threading.Lock()
        self._semaphores = {}

    @contextlib.contextmanager
    def acquire(self, host, blocking=True):
        with self._guard:
            semaphore = self._semaphores.setdefault(
                host, threading.BoundedSemaphore(self._per_host)
            )
        acquired = semaphore.acquire(blocking=blocking)
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            semaphore.release()


_DEFAULT_LIMITER = SiteLimiter()


class _Response:
    """把 bytes 或类文件对象统一成带 status/headers/read/close 的响应。"""

    def __init__(self, raw):
        if isinstance(raw, (bytes, bytearray, memoryview)):
            self._stream = io.BytesIO(bytes(raw))
            self.status = None
            self.headers = {}
            return
        self._stream = raw
        self.status = getattr(raw, "status", None) or getattr(raw, "code", None)
        self.headers = getattr(raw, "headers", None) or {}

    def read(self, size=-1):
        return self._stream.read(size)

    def close(self):
        close = getattr(self._stream, "close", None)
        if callable(close):
            close()


def _header(response, name, default=None):
    headers = response.headers
    if isinstance(headers, dict):
        target = name.lower()
        for key, value in headers.items():
            if key.lower() == target:
                return value
        return default
    getter = getattr(headers, "get", None)
    if getter is None:
        return default
    try:
        return getter(name, default)
    except TypeError:
        return getter(name.lower(), default)


def _int_header(response, name):
    value = _header(response, name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _make_invoker(open_url):
    """兼容只接受 url 单参数的旧式 open_url。"""
    try:
        parameters = inspect.signature(open_url).parameters
    except (TypeError, ValueError):
        supports_headers = True
    else:
        supports_headers = "headers" in parameters or any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in parameters.values()
        )

    if supports_headers:
        return lambda url, headers: open_url(url, headers=headers)
    return lambda url, headers: open_url(url)


def _default_open_url(url, headers=None):
    return urlrequest.urlopen(urlrequest.Request(url, headers=headers or {}))


def _drain(response):
    while True:
        chunk = response.read(CHUNK_SIZE)
        if not chunk:
            return


def _supports_range(invoke, url, host, limiter):
    """用 bytes=0-0 探测；206 或 Accept-Ranges: bytes 均视为支持。"""
    try:
        with limiter.acquire(host, blocking=False) as acquired:
            if not acquired:
                logger.info("站点 %s 并发已满，跳过 range 探测: %s", host, url)
                return None
            response = _Response(invoke(url, {"Range": "bytes=0-0"}))
            try:
                status = response.status
                accept_ranges = str(_header(response, "Accept-Ranges", "")).lower()
                supported = status == 206 or accept_ranges == "bytes"
                _drain(response)
            finally:
                response.close()
    except Exception as exc:  # 探测失败按不支持处理，走整包重下
        logger.warning("range 探测失败，按不支持续传处理 %s: %s", url, exc)
        return False
    if not supported:
        logger.info("服务端不支持范围请求，将整包重下: %s", url)
    return supported


def _resolve_paths(dest, url):
    """dest 为目录时按内容哈希命名，否则直接使用 dest 路径。"""
    if os.path.isdir(dest):
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        part_path = os.path.join(dest, url_hash + PART_SUFFIX)
        extension = os.path.splitext(urlsplit(url).path)[1]
        return part_path, extension
    return dest + PART_SUFFIX, None


def _hash_existing(path, *hashers):
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                return
            for hasher in hashers:
                hasher.update(chunk)


def download(url, dest, open_url=None, checksum=None, limiter=None):
    """下载 url 到 dest，返回落盘字节数。

    - dest 是目录时，最终文件名为 ``<内容sha256><原扩展名>``，避免重名覆盖。
    - 下载中的数据写入 ``.part`` 临时文件；校验失败抛 VerificationError 并保留。
    - checksum 为 ``(算法, 十六进制摘要)`` 时做完整文件校验。
    - limiter 为 SiteLimiter，按主机名限流，默认每站点并发 2。
    """
    if open_url is None:
        open_url = _default_open_url
    if limiter is None:
        limiter = _DEFAULT_LIMITER
    invoke = _make_invoker(open_url)

    parsed = urlsplit(url)
    host = parsed.hostname or parsed.netloc
    part_path, extension = _resolve_paths(dest, url)
    parent = os.path.dirname(part_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    verify_algorithm, verify_digest = checksum if checksum else (None, None)
    name_hasher = hashlib.sha256()
    verify_hasher = hashlib.new(verify_algorithm) if verify_algorithm else None

    range_supported = _supports_range(invoke, url, host, limiter)
    resume_from = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    if resume_from and range_supported is False:
        logger.warning("服务端不支持范围请求，丢弃 %d 字节临时文件并重下: %s",
                       resume_from, url)
        resume_from = 0
    elif resume_from and range_supported is None:
        logger.info("未完成 range 探测，尝试基于临时文件续传: %s", url)

    with limiter.acquire(host):
        headers = {"Range": "bytes=%d-" % resume_from} if resume_from else {}
        response = _Response(invoke(url, headers))
        resumed = bool(resume_from) and response.status == 206
        if resume_from and not resumed:
            resume_from = 0

        if resumed:
            _hash_existing(part_path, *[
                hasher for hasher in (name_hasher, verify_hasher) if hasher
            ])

        total = resume_from
        try:
            with open(part_path, "ab" if resumed else "wb") as handle:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    name_hasher.update(chunk)
                    if verify_hasher is not None:
                        verify_hasher.update(chunk)
                    total += len(chunk)
        finally:
            response.close()

        content_length = _int_header(response, "Content-Length")

    expected_length = None
    if content_length is not None:
        expected_length = resume_from + content_length if resumed else content_length
    if expected_length is not None and total != expected_length:
        message = "长度校验失败 %s: 期望 %d，实际 %d，临时文件 %s" % (
            url, expected_length, total, part_path)
        logger.error(message)
        raise VerificationError(message)

    if verify_hasher is not None and verify_hasher.hexdigest() != verify_digest:
        message = "校验和失败 %s: 期望 %s，实际 %s，临时文件 %s" % (
            url, verify_digest, verify_hasher.hexdigest(), part_path)
        logger.error(message)
        raise VerificationError(message)

    if extension is None:
        final_path = dest
    else:
        final_path = os.path.join(dest, name_hasher.hexdigest() + extension)
    os.replace(part_path, final_path)
    logger.info("下载完成 %s -> %s (%d 字节)", url, final_path, total)
    return total
