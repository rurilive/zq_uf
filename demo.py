from guardwire import Request, Rule, inspect_request

rules = [
    Rule("admin", path_prefix="/admin"),
    Rule("secret", needle="password"),
    Rule(
        "chunked-cmd",
        path_prefix="/api",
        method="POST",
        body_field="cmd",
        body_value="drop",
    ),
]

requests = (
    Request("GET", "/ok"),
    Request("GET", "/%2561dmin/users"),
    Request("POST", "/login", body=b"password=1"),
    Request(
        "POST",
        "/api",
        {"Content-Type": "application/json"},
        body=[b'{"cmd":', b'"drop table users"}'],
    ),
)

for request in requests:
    result = inspect_request(request, rules)
    print(
        "%s %-24s -> allowed=%s rule_id=%s match=%s"
        % (
            request.method,
            request.url,
            result["allowed"],
            result["rule_id"],
            result["evidence"]["match"].get("field"),
        )
    )
