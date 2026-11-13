import http.server
import threading

from detailcrawl import crawl

PAGES = {"http://shop/1": "键盘详情", "http://shop/2": "鼠标详情"}
print(crawl(["http://shop/1", "http://shop/2"], PAGES.get))

TEMPLATES = [
    '<html><head><title>{name}</title></head><body>'
    '<span data-field="price">{price}</span>'
    '<span data-field="stock">{stock}</span></body></html>',
    '<html><head><title>{name}</title></head><body><div>'
    '<b data-field="price">{price}</b>'
    '<i data-field="stock">{stock}</i></div></body></html>',
    '<html><body><h1 data-field="name">{name}</h1>'
    '<p data-field="price">{price}</p>'
    '<p data-field="stock">{stock}</p></body></html>',
]


class DemoHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _respond(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        try:
            n = int(self.path.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            self._respond(404)
            return
        if n % 50 == 0:  # 永久缺失：跳过，不重试
            self._respond(404)
        elif n % 37 == 0:  # 持续 500：重试后记失败
            self._respond(500)
        else:
            body = TEMPLATES[n % 3].format(
                name=f"商品{n}", price=f"{n * 10}.9", stock=n * 3
            ).encode("utf-8")
            self._respond(200, body)


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    urls = [f"{base}/item/{n}" for n in range(1, 201)]
    result = crawl(urls, max_per_host=8)
    print(result.stats.details())
    print("模板分组:", result.stats.templates)
    print("示例字段:", result[0])
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
