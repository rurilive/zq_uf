import os

from tenantcrawl import run
from tenantcrawl import tenant as module

run("acme", [{"id": 1}])
run("beta", [{"id": 2}])
print(module.read_all())
os.remove(os.path.join(module.RESULTS, "out.json"))

# TODO: 第二段演示（实现后补）
