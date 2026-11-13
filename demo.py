from datacontract import BreakingChangeError, Contract, Registry


def show(title, report):
    print("== %s: %s ==" % (title, report["verdict"]))
    for level in ("compatible", "hints", "warnings", "breaking"):
        for item in report[level]:
            line = "  [%s] %s: %s" % (level, item["field"], item["change"])
            if item.get("detail"):
                line += " (%s)" % item["detail"]
            if item.get("consumers"):
                line += " -> consumers: %s" % ", ".join(item["consumers"])
            print(line)


registry = Registry()

v1 = Contract("orders", "1.0", [
    {"name": "id", "type": "int", "nullable": False},
    {"name": "amount", "type": "float", "nullable": False},
    {"name": "status", "type": "str", "enum": ["paid", "shipped"]},
], consumers={"billing": ["id", "amount"], "search": None})

v2 = Contract("orders", "1.1", [
    {"name": "id", "type": "int", "nullable": False},
    {"name": "amount", "type": "float", "nullable": False},
    {"name": "status", "type": "str", "enum": ["paid", "shipped", "refunded"]},
    {"name": "note", "type": "str", "nullable": True},
], consumers=v1.consumers)

v3 = Contract("orders", "2.0", [
    {"name": "id", "type": "int", "nullable": False},
    {"name": "amount", "type": "int", "nullable": False},
    {"name": "status", "type": "str", "enum": ["paid", "shipped", "refunded"]},
], consumers=v1.consumers)

registry.register(v1)
registry.register(v2)
show("v1.0 -> v1.1", registry.diff(v1, v2))

show("v1.1 -> v2.0 (check)", registry.check(v3))
try:
    registry.register(v3)
except BreakingChangeError as exc:
    print("register blocked:", exc)
registry.register(v3, allow_breaking=True)
print("history:", registry.history("orders"))
print("rollback:", registry.rollback("orders", "1.1").version)
print("billing subscribes:", registry.for_consumer("billing"))

rows = [
    {"id": 1, "amount": 9.9, "status": "paid"},
    {"id": 2, "amount": "abc", "status": "lost"},
]
print("validate:", registry.validate("orders", rows))
