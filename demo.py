"""用本地证书模拟三类证书错误和临时放行流程。

运行: python3 demo.py
证书在 demo_certs/（可用 demo_certs/gen.sh 重新生成，仅供演示）。
"""

import http.server
import json
import logging
import os
import ssl
import tempfile
import threading

from tlssafe import (
    CERT_EXPIRED,
    CERT_HOSTNAME_MISMATCH,
    CERT_INCOMPLETE_CHAIN,
    CertError,
    allow_host,
    fetch,
    set_audit_log,
    set_ca_file,
)

CERTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_certs")

logging.basicConfig(level=logging.CRITICAL)  # demo 只保留 print 输出


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def serve(cert_name):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(
        os.path.join(CERTS, cert_name),
        os.path.join(CERTS, cert_name.replace(".pem", ".key")),
    )
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    set_ca_file(os.path.join(CERTS, "rootCA.pem"))
    cases = [
        ("证书过期", "expired.pem", CERT_EXPIRED),
        ("主机名不匹配", "mismatch.pem", CERT_HOSTNAME_MISMATCH),
        ("链不完整", "chain-leaf.pem", CERT_INCOMPLETE_CHAIN),
    ]
    servers = []
    expired_url = None
    print("== 三类证书错误（不重试，直接拒绝） ==")
    for label, cert_name, expect_kind in cases:
        server = serve(cert_name)
        servers.append(server)
        url = f"https://localhost:{server.server_address[1]}/"
        if cert_name == "expired.pem":
            expired_url = url
        try:
            fetch(url)
            raise AssertionError(f"{label}: 预期 CertError，请求却成功了")
        except CertError as exc:
            assert exc.kind == expect_kind, f"{exc.kind} != {expect_kind}"
            assert exc.action == "rejected", exc.action
            print(f"{label}: kind={exc.kind} action={exc.action}")
            print(f"  站点={exc.url}")
            print(f"  证书主题={exc.cert_subject} 到期={exc.cert_expires}")

    print("== 临时放行（写审计） ==")
    audit_path = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
    set_audit_log(audit_path)
    allow_host("localhost", minutes=5, reason="demo: 证书续期前临时放行")
    resp = fetch(expired_url)
    print(f"放行结果: HTTP {resp.status} body={resp.read().decode()}")
    with open(audit_path, encoding="utf-8") as fh:
        for line in fh:
            record = json.loads(line)
            assert record["result"] == "bypassed", record
            print(f"审计: event={record['event']} host={record['host']} "
                  f"kind={record['kind']} result={record['result']}")

    for server in servers:
        server.shutdown()
    print("demo 完成")


if __name__ == "__main__":
    main()
