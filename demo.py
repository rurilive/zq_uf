"""两个租户并行采集：配置、队列、结果目录和配额各自隔离。"""
import shutil
import threading

from tenantcrawl import tenant as tc

tc.reset()
tc.configure("acme", workers=2, quota=100, rate_per_sec=200)
# beta 不配置，使用默认值 workers=4 / quota=1000 / rate_per_sec=100

jobs = [
    threading.Thread(target=tc.run, args=("acme", [{"id": i} for i in range(6)])),
    threading.Thread(target=tc.run, args=("beta", [{"id": i} for i in range(4)])),
]
for job in jobs:
    job.start()
for job in jobs:
    job.join()

for name in ("acme", "beta"):
    info = tc.stats(name)
    print(
        f"[{name}] tasks={info['tasks_completed']} workers={info['workers']} "
        f"quota={info['quota']} rate={info['rate_per_sec']}/s "
        f"elapsed={info['elapsed_total']:.3f}s dir={info['results_dir']}"
    )
    tenants = {row["tenant"] for row in tc.read_results(name)}
    print(f"[{name}] 本租户结果只含: {sorted(tenants)}")

try:
    tc.read_results("acme", "../beta/out.json")
except tc.TenantAccessError as exc:
    print(f"越权读取被拒绝: {exc}")

shutil.rmtree(tc.RESULTS, ignore_errors=True)
