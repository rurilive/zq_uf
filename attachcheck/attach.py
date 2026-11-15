"""附件下载：断点续传、内容寻址去重、长度与内容校验、隔离区。

对外只暴露三个动作：

- ``fetch(url, path)``：下载。先写中间文件，断线后从断点继续，校验通过
  才原子改名到正式文件名；同名但内容不同的附件用内容哈希区分；同一地址
  重复下载直接复用已下载的文件。
- ``verify(path)``：按记录的长度和内容校验文件。
- ``AttachmentStore.retry(entry)``：重试隔离区里的失败记录。

目标目录只会出现完整文件。中间文件、断点、校验记录和隔离区都在目标
目录下的 ``.attachcheck/`` 中。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import json
import os
import re
import shutil
import time
import uuid

from .transport import TransportError, UrllibTransport

STATE_DIR = ".attachcheck"
_DEFAULT_CHUNK = 256 * 1024
_SYNC_EVERY = 1024 * 1024
_MAX_RESTARTS = 2
_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$")
_UNSATISFIED = re.compile(r"^bytes\s+\*/(\d+)$")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _hash_file(path, limit=None):
    """返回 (sha256, 实际读取字节数)；limit 用于校验已下载前缀。"""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            remaining = None if limit is None else limit - size
            if remaining is not None and remaining <= 0:
                break
            block = handle.read(_DEFAULT_CHUNK if remaining is None
                                else min(_DEFAULT_CHUNK, remaining))
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _as_int(value):
    """把记录里的数字安全读成 int；坏记录返回 None 而不是抛异常。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _write_json(path, payload):
    tmp = f"{path}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _remove(*paths):
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def _content_range_total(value):
    if not value:
        return None
    value = value.strip()
    match = _CONTENT_RANGE.match(value)
    if match:
        return None if match.group(3) == "*" else int(match.group(3))
    match = _UNSATISFIED.match(value)
    return int(match.group(1)) if match else None


def _content_range_start(value):
    match = _CONTENT_RANGE.match((value or "").strip())
    return int(match.group(1)) if match else None


def _content_length(headers):
    raw = headers.get("content-length")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _server_digests(headers):
    """服务器声明的校验值 {算法: hex}，支持 Digest 头和 Content-MD5。"""
    found = {}
    for item in (headers.get("digest") or "").split(","):
        name, _, raw = item.strip().partition("=")
        raw = raw.strip().strip(":")
        if not name or not raw:
            continue
        try:
            blob = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error):
            continue
        algorithm = name.strip().lower().replace("-", "")
        if algorithm in {"sha256", "sha512", "md5"}:
            found[algorithm] = blob.hex()
    raw = headers.get("content-md5")
    if raw:
        try:
            found.setdefault("md5", base64.b64decode(raw.strip(), validate=True).hex())
        except (ValueError, binascii.Error):
            pass
    return found


def _digest_matches(digests, path):
    """按服务器声明的算法复核文件；无声明时返回 True。"""
    for algorithm, expected in digests.items():
        hasher = hashlib.new(algorithm)
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(_DEFAULT_CHUNK), b""):
                hasher.update(block)
        if hasher.hexdigest().lower() != expected.lower():
            return False
    return True


def _host_of(url):
    try:
        netloc = url.split("//", 1)[1].split("/", 1)[0]
    except IndexError:
        return ""
    return netloc.split("@")[-1].split(":")[0].lower()


class AttachmentStore:
    """一个附件根目录下的下载器。

    ``canary_sites`` 为 None 时所有站点都启用续传与隔离；给定站点集合时，
    只有这些主机启用，其余站点走不带续传/隔离的单次下载，用于先在一个
    站点观察新行为、确认正常附件不受影响。
    """

    def __init__(self, root, transport=None, *, canary_sites=None,
                 chunk_size=_DEFAULT_CHUNK, timeout=10.0):
        self.root = os.path.abspath(root)
        self.transport = transport or UrllibTransport()
        self.canary_sites = None if canary_sites is None else {s.lower() for s in canary_sites}
        self.chunk_size = max(1, int(chunk_size))
        self.timeout = timeout
        self.state_dir = os.path.join(self.root, STATE_DIR)
        self.parts_dir = os.path.join(self.state_dir, "parts")
        self.records_dir = os.path.join(self.state_dir, "records")
        self.by_path_dir = os.path.join(self.state_dir, "by-path")
        self.by_url_dir = os.path.join(self.state_dir, "by-url")
        self.quarantine_dir = os.path.join(self.state_dir, "quarantine")

    # ------------------------------------------------------------------ 对外

    def fetch(self, url, path, *, refresh=False):
        """下载 ``url`` 到 ``path``；``refresh=True`` 跳过复用，强制重新下载。"""
        self._ensure()
        target = os.path.abspath(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        observed = self._observed(url)

        if not refresh:
            reused = self._reuse(url)
            if reused is not None:
                return reused
        if not observed:
            return self._download(url, target, allow_resume=False,
                                  allow_quarantine=False)

        # 目标位置上如果是我们以前下的文件但已损坏，先挪进隔离区腾出正式名字；
        # 不是我们下的同名文件则保持不动，新内容稍后用哈希后缀区分。
        self._quarantine_damaged_target(target)
        result = self._download(url, target, allow_resume=True, allow_quarantine=True)
        if result["ok"]:
            self._drop_entry_for(url, target)
        return result

    def verify(self, path, *, quarantine=False):
        """按记录的长度和内容校验；``quarantine=True`` 时把坏文件移入隔离区。"""
        target = os.path.abspath(path)
        record = _read_json(self._by_path_file(target))
        checksum = size = None
        if os.path.isfile(target):
            size = os.path.getsize(target)
            checksum, _ = _hash_file(target)
        result = {
            "path": target, "ok": False, "known": record is not None,
            "checksum": checksum, "size": size,
            "expected_size": None, "expected_checksum": None, "reason": None,
        }
        if record is None:
            result["reason"] = "no_record"
            return result
        expected_size = record.get("size")
        expected_checksum = record.get("sha256")
        result.update(expected_size=expected_size, expected_checksum=expected_checksum)
        if not os.path.isfile(target):
            result["reason"] = "missing"
        elif expected_size is not None and size != expected_size:
            result["reason"] = "length_mismatch"
        elif expected_checksum and checksum != expected_checksum:
            result["reason"] = "checksum_mismatch"
        else:
            result["ok"] = True
        if result["reason"] and quarantine:
            entry = self._quarantine_local_file(target, record, result["reason"])
            result["quarantine"] = entry["id"]
        return result

    def list_quarantine(self):
        """按更新时间列出隔离区记录，附带被扣下的内容路径。"""
        entries = []
        try:
            names = sorted(os.listdir(self.quarantine_dir))
        except OSError:
            return entries
        for name in names:
            entry = _read_json(os.path.join(self.quarantine_dir, name, "entry.json"))
            if entry:
                entry["payload"] = self._payload_path(entry)
                entries.append(entry)
        entries.sort(key=lambda item: item.get("updated_at", ""))
        return entries

    def quarantine_entry(self, entry_id):
        """按 id 读取隔离区记录（``fetch`` 结果的 ``quarantine`` 字段）。"""
        return self._entry(entry_id)

    def retry(self, entry):
        """重试隔离区记录：成功则清除记录，失败则累加次数并保留原始地址。"""
        record = self._entry(entry)
        if record is None:
            return {"ok": False, "error": "no such quarantine entry",
                    "entry": None, "path": None, "bytes": 0}
        result = self.fetch(record["url"], record["target"], refresh=True)
        if result.get("ok"):
            self._drop_entry(record["id"])
        else:
            result["entry"] = record["id"]
        return result

    # -------------------------------------------------------------- 路径与状态

    def _ensure(self):
        for directory in (self.root, self.parts_dir, self.records_dir,
                          self.by_path_dir, self.by_url_dir, self.quarantine_dir):
            os.makedirs(directory, exist_ok=True)

    def _observed(self, url):
        return self.canary_sites is None or _host_of(url) in self.canary_sites

    def _part_paths(self, url, target):
        key = _sha256_hex(f"{url}\0{target}".encode("utf-8"))[:32]
        return (os.path.join(self.parts_dir, key + ".part"),
                os.path.join(self.parts_dir, key + ".json"))

    def _by_path_file(self, path):
        key = _sha256_hex(path.encode("utf-8"))[:32]
        return os.path.join(self.by_path_dir, key + ".json")

    def _record_file(self, checksum):
        return os.path.join(self.records_dir, checksum + ".json")

    def _by_url_file(self, url):
        return os.path.join(self.by_url_dir, _sha256_hex(url.encode("utf-8"))[:32] + ".json")

    def _record_for_url(self, url):
        record = _read_json(self._by_url_file(url))
        return record if isinstance(record, dict) else None

    def _remember(self, url, record):
        self._ensure()
        _write_json(self._record_file(record["sha256"]), record)
        _write_json(self._by_path_file(record["path"]), record)
        _write_json(self._by_url_file(url), record)

    def _reuse(self, url):
        """同一地址重复下载时直接复用，文件内容必须仍然匹配记录。"""
        record = self._record_for_url(url)
        if not record:
            return None
        path = record.get("path")
        checksum = record.get("sha256")
        size = record.get("size")
        if not (path and checksum and size is not None and os.path.isfile(path)):
            return None
        try:
            if os.path.getsize(path) != size:
                return None
            actual, _ = _hash_file(path)
        except OSError:
            return None
        if actual != checksum:
            return None
        return self._result(url, path=path, size=size, received=0, total=size,
                            resumed=False, reused=True, ok=True, status=None,
                            error=None, quarantine=None, observed=self._observed(url),
                            checksum=checksum)

    def _result(self, url, *, path, size, received, total, resumed, reused, ok,
                status, error, quarantine, observed, checksum, reason=None,
                expected_size=None, expected_checksum=None, etag=None):
        return {
            "path": path,
            "bytes": size,
            "received": received,
            "total": total,
            "resumed": resumed,
            "reused": reused,
            "ok": ok,
            "url": url,
            "status": status,
            "error": error,
            "quarantine": quarantine,
            "observed": observed,
            "mode": "canary" if observed else "simple",
            "etag": etag,
            "verified": {
                "ok": ok,
                "known": True,
                "checksum": checksum,
                "size": size,
                "expected_size": size if expected_size is None else expected_size,
                "expected_checksum": checksum if expected_checksum is None else expected_checksum,
                "reason": reason,
            },
        }

    # ---------------------------------------------------------------- 中间文件

    def _checkpoint(self, meta_path, meta, offset, hasher):
        """把断点和已下载前缀的哈希落盘，供下次续传时校验。"""
        meta.update(offset=offset, prefix_sha256=hasher.hexdigest(), updated_at=_now())
        _write_json(meta_path, meta)

    def _load_part(self, url, part_path, meta_path):
        """返回 (offset, hasher, meta)，无法可信续传时返回 None。"""
        meta = _read_json(meta_path)
        if not meta or meta.get("url") != url or not os.path.isfile(part_path):
            _remove(part_path, meta_path)
            return None
        offset = _as_int(meta.get("offset"))
        size = os.path.getsize(part_path)
        if offset is None or offset <= 0 or size < offset:
            _remove(part_path, meta_path)
            return None
        if size > offset:
            # 断线时多写、没来得及落盘的尾巴，截回已校验的断点。
            with open(part_path, "r+b") as handle:
                handle.truncate(offset)
        checksum, length = _hash_file(part_path, limit=offset)
        if length != offset or checksum != meta.get("prefix_sha256"):
            _remove(part_path, meta_path)
            return None
        hasher = hashlib.sha256()
        with open(part_path, "rb") as handle:
            for block in iter(lambda: handle.read(self.chunk_size), b""):
                hasher.update(block)
        return offset, hasher, meta

    def _discard_part(self, part_path, meta_path):
        _remove(part_path, meta_path)

    # ------------------------------------------------------------------ 下载

    def _download(self, url, target, *, allow_resume, allow_quarantine):
        self._ensure()
        part_path, meta_path = self._part_paths(url, target)

        loaded = self._load_part(url, part_path, meta_path) if allow_resume else None
        if loaded:
            offset, hasher, meta = loaded
            resumed = True
        else:
            self._discard_part(part_path, meta_path)
            offset, hasher = 0, hashlib.sha256()
            resumed = False
            meta = {"url": url, "etag": None, "last_modified": None,
                    "total": None, "created_at": _now()}

        received = 0
        restarts = 0
        status = None
        total = meta.get("total") if isinstance(meta.get("total"), int) else None

        # 上次已经下完、只是还没来得及改名：直接收尾，不再请求站点。
        if (allow_resume and offset and total is not None and offset == total
                and os.path.isfile(part_path)):
            return self._finish(
                url, target, part_path, meta_path, meta, status=None, size=offset,
                resumed=True, received=0, allow_quarantine=allow_quarantine,
                allow_resume=True)

        while True:
            try:
                response = self.transport.open(
                    url, offset=offset, etag=meta.get("etag"),
                    last_modified=meta.get("last_modified"), timeout=self.timeout)
            except TransportError as exc:
                return self._fail(
                    url, target, part_path, meta_path, meta, status=None,
                    reason="transport", error=str(exc), size=self._part_size(part_path),
                    resumed=resumed, allow_quarantine=allow_quarantine,
                    received=received, allow_resume=allow_resume)

            try:
                status = response.status
                if status == 200:
                    if offset:
                        # 服务器忽略了 Range，只能从头重下。
                        offset, hasher, resumed = 0, hashlib.sha256(), False
                        meta.update(etag=None, last_modified=None, total=None)
                        total = None
                        self._discard_part(part_path, meta_path)
                    total = (_content_range_total(response.header("content-range"))
                             or _content_length(response.headers) or total)
                elif status == 206:
                    start = _content_range_start(response.header("content-range"))
                    response_total = _content_range_total(response.header("content-range"))
                    etag_now = response.header("etag")
                    changed = (
                        start != offset
                        or (total is not None and response_total is not None
                            and total != response_total)
                        or (meta.get("etag") and etag_now and meta["etag"] != etag_now)
                    )
                    if changed:
                        if restarts >= _MAX_RESTARTS:
                            return self._fail(
                                url, target, part_path, meta_path, meta, status=status,
                                reason="range_mismatch",
                                error="server returned an inconsistent range",
                                size=self._part_size(part_path), resumed=resumed,
                                allow_quarantine=allow_quarantine,
                                received=received, allow_resume=allow_resume)
                        restarts += 1
                        offset, hasher, resumed = 0, hashlib.sha256(), False
                        meta.update(etag=None, last_modified=None, total=None)
                        total = None
                        self._discard_part(part_path, meta_path)
                        continue
                    total = response_total if response_total is not None else total
                elif status == 416:
                    response_total = _content_range_total(response.header("content-range"))
                    part_size = self._part_size(part_path)
                    if (response_total is not None and offset and part_size == response_total):
                        # 站点声明总长就等于已下载长度：断点已经完整，直接收尾。
                        total = response_total
                        return self._finish(
                            url, target, part_path, meta_path, meta, status=status,
                            size=part_size, resumed=resumed, received=received,
                            allow_quarantine=allow_quarantine, allow_resume=allow_resume)
                    if restarts < _MAX_RESTARTS:
                        restarts += 1
                        offset, hasher, resumed = 0, hashlib.sha256(), False
                        meta.update(etag=None, last_modified=None, total=None)
                        total = None
                        self._discard_part(part_path, meta_path)
                        continue
                    return self._fail(
                        url, target, part_path, meta_path, meta, status=416,
                        reason="http_status", error="range not satisfiable",
                        size=self._part_size(part_path), resumed=resumed,
                        allow_quarantine=allow_quarantine,
                        received=received, allow_resume=allow_resume)
                else:
                    return self._fail(
                        url, target, part_path, meta_path, meta, status=status,
                        reason="http_status", error=f"HTTP {status}",
                        size=self._part_size(part_path), resumed=resumed,
                        allow_quarantine=allow_quarantine,
                        received=received, allow_resume=allow_resume)

                self._store_response_meta(meta, response)
                meta["total"] = total
                written, hasher, received, failure = self._stream(
                    response, part_path, meta_path, meta, offset, hasher)
            finally:
                response.close()

            if failure is not None:
                return self._fail(
                    url, target, part_path, meta_path, meta, status=status,
                    reason="disconnect", error=failure, size=written,
                    resumed=resumed, allow_quarantine=allow_quarantine,
                    received=received, allow_resume=allow_resume)
            if total is not None and written < total:
                # 响应干净结束但长度不够：按断线处理，保住断点等下次续传。
                return self._fail(
                    url, target, part_path, meta_path, meta, status=status,
                    reason="incomplete",
                    error=f"expected {total} bytes, got {written}", size=written,
                    resumed=resumed, allow_quarantine=allow_quarantine,
                    received=received, allow_resume=allow_resume)
            if total is not None and written > total:
                return self._bad_file(
                    url, target, part_path, meta_path, meta, size=written,
                    reason="length_mismatch",
                    error=f"expected {total} bytes, got {written}",
                    resumed=resumed, allow_quarantine=allow_quarantine,
                    received=received)
            return self._finish(
                url, target, part_path, meta_path, meta, status=status, size=written,
                resumed=resumed, received=received, allow_quarantine=allow_quarantine,
                allow_resume=allow_resume)

    @staticmethod
    def _store_response_meta(meta, response):
        if response.header("etag"):
            meta["etag"] = response.header("etag")
        if response.header("last-modified"):
            meta["last_modified"] = response.header("last-modified")
        digests = _server_digests(response.headers)
        if digests:
            meta["server_digests"] = digests

    def _stream(self, response, part_path, meta_path, meta, offset, hasher):
        """把响应体追加到中间文件；返回 (已写字节, hasher, 本次收到字节, 错误)。"""
        written = offset
        received = 0
        since_sync = 0
        failure = None
        mode = "ab" if offset else "wb"
        with open(part_path, mode) as handle:
            while True:
                try:
                    block = response.read(self.chunk_size)
                except (ConnectionResetError, TimeoutError, OSError,
                        http.client.HTTPException) as exc:
                    failure = f"{type(exc).__name__}: {exc}"
                    break
                if not block:
                    break
                handle.write(block)
                hasher.update(block)
                written += len(block)
                received += len(block)
                since_sync += len(block)
                if since_sync >= _SYNC_EVERY:
                    handle.flush()
                    os.fsync(handle.fileno())
                    self._checkpoint(meta_path, meta, written, hasher)
                    since_sync = 0
            handle.flush()
            os.fsync(handle.fileno())
        self._checkpoint(meta_path, meta, written, hasher)
        return written, hasher, received, failure

    def _finish(self, url, target, part_path, meta_path, meta, *, status, size,
                resumed, received, allow_quarantine, allow_resume):
        checksum, _ = _hash_file(part_path)
        digests = meta.get("server_digests") or {}
        if digests and not _digest_matches(digests, part_path):
            return self._bad_file(
                url, target, part_path, meta_path, meta, size=size,
                reason="checksum_mismatch", error="server digest mismatch",
                resumed=resumed, allow_quarantine=allow_quarantine,
                received=received)

        final, reused = self._publish(part_path, target, checksum, size)
        _remove(meta_path)
        self._remember(url, {
            "url": url, "sha256": checksum, "size": size, "path": final,
            "etag": meta.get("etag"), "last_modified": meta.get("last_modified"),
            "downloaded_at": _now(),
        })
        return self._result(
            url, path=final, size=size, received=received,
            total=meta.get("total") if meta.get("total") is not None else size,
            resumed=resumed, reused=reused, ok=True, status=status, error=None,
            quarantine=None, observed=allow_quarantine, checksum=checksum,
            etag=meta.get("etag"))
    def _publish(self, part_path, requested, checksum, size):
        """把中间文件原子地放到正式名字；同名不同内容时用内容哈希区分。"""
        stem, suffix = os.path.splitext(requested)
        candidates = [requested, f"{stem}.{checksum[:12]}{suffix}"]
        candidates += [f"{stem}.{checksum[:12]}-{i}{suffix}" for i in range(1, 1000)]
        for candidate in candidates:
            if os.path.exists(candidate):
                if self._matches(candidate, checksum, size):
                    _remove(part_path)
                    return candidate, True
                continue
            try:
                os.link(part_path, candidate)
            except FileExistsError:
                if self._matches(candidate, checksum, size):
                    _remove(part_path)
                    return candidate, True
                continue
            except OSError:
                # 文件系统不支持硬链接：独占创建后复制，同样不会覆盖既有文件。
                try:
                    with open(candidate, "xb") as dst, open(part_path, "rb") as src:
                        shutil.copyfileobj(src, dst, _DEFAULT_CHUNK)
                        dst.flush()
                        os.fsync(dst.fileno())
                except FileExistsError:
                    if self._matches(candidate, checksum, size):
                        _remove(part_path)
                        return candidate, True
                    continue
                _remove(part_path)
                return candidate, False
            _remove(part_path)
            return candidate, False
        raise RuntimeError(f"cannot allocate a final name near {requested}")

    @staticmethod
    def _matches(path, checksum, size):
        try:
            if os.path.getsize(path) != size:
                return False
            actual, _ = _hash_file(path)
        except OSError:
            return False
        return actual == checksum

    @staticmethod
    def _part_size(part_path):
        try:
            return os.path.getsize(part_path)
        except OSError:
            return 0

    # ---------------------------------------------------------------- 隔离区

    def _bad_file(self, url, target, part_path, meta_path, meta, *, size,
                  reason, error, resumed, allow_quarantine, received):
        entry = None
        if allow_quarantine:
            entry = self._record_failure(
                url=url, target=target, status=None, reason=reason, error=error,
                size=size, resumed=resumed, meta=meta, payload_path=part_path)
            _remove(meta_path)
        else:
            self._discard_part(part_path, meta_path)
        result = self._result(
            url, path=None, size=size, received=received,
            total=meta.get("total"), resumed=resumed, reused=False, ok=False,
            status=None, error=error,
            quarantine=entry["id"] if entry else None,
            observed=allow_quarantine, checksum=None, reason=reason,
            expected_size=meta.get("total"))
        return result

    def _fail(self, url, target, part_path, meta_path, meta, *, status, reason,
              error, size, resumed, allow_quarantine, received, allow_resume):
        entry = None
        if allow_quarantine:
            entry = self._record_failure(
                url=url, target=target, status=status, reason=reason, error=error,
                size=size, resumed=resumed, meta=meta)
        if not allow_resume:
            self._discard_part(part_path, meta_path)
        return self._result(
            url, path=None, size=size, received=received, total=meta.get("total"),
            resumed=resumed, reused=False, ok=False, status=status, error=error,
            quarantine=entry["id"] if entry else None, observed=allow_quarantine,
            checksum=None, reason=reason, expected_size=meta.get("total"))

    def _record_failure(self, *, url, target, status, reason, error, size,
                        resumed, meta, payload_path=None):
        """写隔离区记录；原始地址、状态码和断点都保留，重试时据此续传。"""
        entry_id = _sha256_hex(f"{url}\0{target}".encode("utf-8"))[:16]
        entry_dir = os.path.join(self.quarantine_dir, entry_id)
        os.makedirs(entry_dir, exist_ok=True)
        previous = _read_json(os.path.join(entry_dir, "entry.json")) or {}
        payload = previous.get("payload")
        if payload_path and os.path.isfile(payload_path):
            payload = "payload" + os.path.splitext(payload_path)[1]
            os.replace(payload_path, os.path.join(entry_dir, payload))
        entry = {
            "id": entry_id,
            "url": url,
            "target": target,
            "status": status,
            "reason": reason,
            "error": error,
            "bytes": size,
            "offset": _as_int((meta or {}).get("offset")) or 0,
            "resumed": resumed,
            "attempts": (_as_int(previous.get("attempts")) or 0) + 1,
            "payload": payload,
            "created_at": previous.get("created_at") or _now(),
            "updated_at": _now(),
        }
        _write_json(os.path.join(entry_dir, "entry.json"), entry)
        return entry

    def _quarantine_damaged_target(self, target):
        """目标文件有下载记录但内容已坏：挪进隔离区，避免它挡住新的下载。"""
        record = _read_json(self._by_path_file(target))
        if not record or not os.path.isfile(target):
            return None
        checksum = record.get("sha256")
        size = record.get("size")
        if checksum and size is not None and self._matches(target, checksum, size):
            return None
        return self._quarantine_local_file(target, record, "checksum_mismatch")

    def _quarantine_local_file(self, target, record, reason):
        entry = self._record_failure(
            url=record.get("url") or "", target=target, status=None,
            reason=reason, error=f"local file failed verification: {reason}",
            size=record.get("size"), resumed=False, meta=None, payload_path=target)
        _remove(self._by_path_file(target))
        return entry

    def _entry(self, entry):
        if isinstance(entry, dict):
            entry = entry.get("id")
        if not entry:
            return None
        path = os.path.join(self.quarantine_dir, os.path.basename(str(entry)), "entry.json")
        return _read_json(path)

    def _payload_path(self, entry):
        if not entry.get("payload"):
            return None
        return os.path.join(self.quarantine_dir, entry["id"], entry["payload"])

    def _drop_entry(self, entry_id):
        entry_dir = os.path.join(self.quarantine_dir, os.path.basename(str(entry_id)))
        try:
            for name in os.listdir(entry_dir):
                _remove(os.path.join(entry_dir, name))
            os.rmdir(entry_dir)
        except OSError:
            pass

    def _drop_entry_for(self, url, target):
        self._drop_entry(_sha256_hex(f"{url}\0{target}".encode("utf-8"))[:16])


def fetch(url, path, *, transport=None, canary_sites=None, refresh=False):
    """便捷入口：以目标目录为根建一个 AttachmentStore 再下载。"""
    store = AttachmentStore(os.path.dirname(os.path.abspath(path)),
                            transport=transport, canary_sites=canary_sites)
    return store.fetch(url, path, refresh=refresh)


def verify(path, *, quarantine=False):
    """便捷入口：按下载记录校验文件长度与内容。"""
    store = AttachmentStore(os.path.dirname(os.path.abspath(path)))
    return store.verify(path, quarantine=quarantine)
