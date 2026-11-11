from guardwire import Request, Rule, inspect_request

rules = [Rule("admin", path_prefix="/admin"), Rule("secret", needle="password")]
for request in (
    Request("GET", "/ok"),
    Request("GET", "/admin/users"),
    Request("POST", "/login", body=b"password=1"),
):
    print(request.method, request.url, inspect_request(request, rules))
