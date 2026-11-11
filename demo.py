from guardwire import Request, Rule, inspect_request

rules = [
    Rule("admin", path_prefix="/admin"),
    Rule("xss", query_param="q=<script>"),
    Rule("role", body_field="role=admin"),
    Rule("secret", needle="password"),
]


def chunked(payload):
    middle = len(payload) // 2
    frames = (payload[:middle], payload[middle:])
    raw = b"".join(
        f"{len(frame):x}\r\n".encode() + frame + b"\r\n" for frame in frames
    )
    return raw + b"0\r\n\r\n"


cases = [
    ("allow: benign path", Request("GET", "/ok")),
    (
        "deny: double-encoded path",
        Request("GET", "/%2561dmin/users"),
    ),
    (
        "deny: dangerous value hidden in repeated params",
        Request("GET", "/search?q=safe&q=%3Cscript%3E&q=fine"),
    ),
    (
        "deny: chunked JSON reassembled then inspected",
        Request(
            "POST",
            "/api",
            {
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
            chunked(b'{"role":"admin"}'),
        ),
    ),
    (
        "deny: malformed JSON is rejected",
        Request(
            "POST",
            "/api",
            {"Content-Type": "application/json"},
            b'{"role":',
        ),
    ),
    (
        "deny: form body keyword",
        Request(
            "POST",
            "/login",
            {"Content-Type": "application/x-www-form-urlencoded"},
            b"user=1&password=secret",
        ),
    ),
]

for label, request in cases:
    result = inspect_request(request, rules)
    verdict = "BLOCK" if not result["allowed"] else "PASS"
    print(f"{verdict} | {label} | rule={result['rule_id']}")
