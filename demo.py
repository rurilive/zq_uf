"""对比两版契约并演示归档回读。"""

import os
import tempfile

from datacontract import check, load, save

v1 = {
    "version": 1,
    "fields": ["id", "amount", "status", "note"],
    "types": {"id": "int", "amount": "float", "status": "str", "note": "str"},
    "required": ["id"],
    "defaults": {"amount": 0.0},
    "enums": {"status": ["open", "closed", "archived"]},
}
v2 = {
    "version": 2,
    "fields": ["id", "amount", "status", "extra"],
    "types": {"id": "int", "amount": "int", "status": "str", "extra": "str"},
    "required": ["id"],
    "defaults": {"amount": 1.0},
    "enums": {"status": ["open", "closed"]},
}

result = check(v1, v2)
print("compatible:", result["compatible"])
print("backward_compatible:", result["backward_compatible"])
print("forward_compatible:", result["forward_compatible"])
for item in result["issues"]:
    print("[%s/%s] %s: %s" % (item["severity"], item["direction"], item["field"], item["message"]))
print("suggestions:")
for suggestion in result["suggestions"]:
    print(" -", suggestion)

with tempfile.TemporaryDirectory() as tmp:
    for contract in (v1, v2):
        save(contract, os.path.join(tmp, "contract_v%s.json" % contract["version"]))
    reread = check(load(os.path.join(tmp, "contract_v1.json")),
                   load(os.path.join(tmp, "contract_v2.json")))
    print("archive roundtrip consistent:", reread == result)

