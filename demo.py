import json
import os
import tempfile

import sessionvault
from sessionvault import load, save

root = tempfile.mkdtemp()
save("acc-1", {"cookie": "sid=abc"}, root=root)
save("acc-2", {"cookie": "sid=def"}, root=root)
print("key_version:", sessionvault.current_key_version(root))
print("load acc-1:", load("acc-1", root=root))

# 模拟轮换失败：目录只读，新版本写不进去，应回滚到旧版本
os.chmod(root, 0o500)
print("rotate (read-only):", sessionvault.rotate(root))
os.chmod(root, 0o700)
print("key_version after rollback:", sessionvault.current_key_version(root))
print("acc-1 still readable:", load("acc-1", root=root))

# 正常轮换
print("rotate:", sessionvault.rotate(root))

# 篡改 acc-2 的密文，触发校验和失败 -> 隔离
path = os.path.join(root, "acc-2.json")
envelope = json.load(open(path, encoding="utf-8"))
envelope["payload"] = ("0" if envelope["payload"][0] != "0" else "1") + envelope["payload"][1:]
json.dump(envelope, open(path, "w", encoding="utf-8"))
try:
    load("acc-2", root=root)
except sessionvault.CorruptRecordError as exc:
    print("corrupt acc-2:", exc.reason)
print("quarantined:", sessionvault.quarantined(root))
print("acc-1 unaffected:", load("acc-1", root=root))
