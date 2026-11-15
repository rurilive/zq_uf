"""脚本化的站点替身：离线演示与测试断线、Range、内容变更和错误码。

它实现 ``UrllibTransport`` 的同一接口（``open`` 返回 ``Response``），
测试里把 ``ScriptedTransport`` 注入 ``AttachmentStore`` 即可完全离线。
"""

from __future__ import annotations

from .transport import Response, TransportError


class _Raw:
    """可读的响应体；``cut_after`` 用来模拟收到一半断线。"""

    def __init__(self, data, cut_after=None):
        self.data = data
        self.pos = 0
        self.limit = len(data) if cut_after is None else min(cut_after, len(data))
        self._cut = cut_after is not None and cut_after < len(data)

    def read(self, size=-1):
        if self.pos >= self.limit:
            if self._cut:
                self._cut = False
                raise ConnectionResetError("scripted disconnect")
            return b""
        end = self.limit if size is None or size < 0 else min(self.limit, self.pos + size)
        block = self.data[self.pos:end]
        self.pos = end
        return block

    def close(self):
        return None


class ScriptedTransport:
    """按 url 预先写好每次请求的行为。

    用法::

        site = ScriptedTransport()
        site.add("https://shop/a.bin", b"x" * 100, etag='"v1"')
        site.script("https://shop/a.bin", {"cut": 30}, {}, {})
        store = AttachmentStore(tmp, transport=site)

    每一步是 dict，支持：
    - ``{"cut": n}``        正常响应，但只发 n 字节后断线
    - ``{"status": 404}``   返回该 HTTP 状态码
    - ``{"error": "..."}``  连接层失败
    - ``{"ignore_range": True}`` 忽略 Range，始终回 200 全量
    - ``{"content": b"..."}``    请求前替换资源内容（模拟上传新版本）
    - ``{"short_body": True}``   响应体少于 Content-Length，然后干净结束
    """

    def __init__(self):
        self.resources = {}
        self.scripts = {}
        self.requests = []

    def add(self, url, content, *, etag=None, last_modified=None):
        self.resources[url] = {
            "content": bytes(content),
            "etag": etag,
            "last_modified": last_modified,
        }

    def script(self, url, *steps):
        self.scripts.setdefault(url, []).extend(dict(step) for step in steps)

    def open(self, url, *, offset=0, etag=None, last_modified=None, timeout=10.0):
        record = {
            "url": url,
            "offset": offset,
            "range": f"bytes={offset}-" if offset else None,
            "if_range": etag or last_modified,
            "status": None,
        }
        self.requests.append(record)

        steps = self.scripts.get(url)
        step = steps.pop(0) if steps else {}
        if "error" in step:
            raise TransportError(step["error"])

        resource = self.resources.get(url)
        if resource is None or "status" in step:
            status = step.get("status", 404)
            record["status"] = status
            return Response(url, status, {"Content-Length": "0"}, _Raw(b""))

        if "content" in step:
            resource = {
                "content": bytes(step["content"]),
                "etag": step.get("etag", resource.get("etag")),
                "last_modified": resource.get("last_modified"),
            }
            self.resources[url] = resource

        content = resource["content"]
        total = len(content)
        validator = resource.get("etag") or resource.get("last_modified")

        # If-Range 校验失败（资源已变）或显式要求忽略 Range 时回全量 200。
        if offset and (step.get("ignore_range") or (record["if_range"] and validator and record["if_range"] != validator)):
            offset = 0
            record["offset"] = 0
            record["range"] = None

        if offset >= total and offset:
            record["status"] = 416
            return Response(url, 416, {"Content-Length": "0", "Content-Range": f"bytes */{total}"}, _Raw(b""))
        if offset:
            body = content[offset:]
            last = total - 1 if total else total
            headers = {
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes {offset}-{last}/{total}",
                "Accept-Ranges": "bytes",
            }
            record["status"] = 206
        else:
            body = content
            headers = {"Content-Length": str(total), "Accept-Ranges": "bytes"}
            record["status"] = 200

        if step.get("short_body"):
            headers["Content-Length"] = str(len(body) + 7)

        if resource.get("etag"):
            headers["ETag"] = resource["etag"]
        if resource.get("last_modified"):
            headers["Last-Modified"] = resource["last_modified"]

        return Response(url, record["status"], headers, _Raw(body, step.get("cut")))
