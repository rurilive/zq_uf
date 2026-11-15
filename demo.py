from proxymesh.mesh import Mesh


class Clock:
    """演示用可推进时钟，真实环境用 time.monotonic。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def show(mesh, site, label):
    picked = mesh.pick(site)
    print("[%s] pick -> %s (room=%s, score=%.3f, switched=%s)"
          % (label, picked["proxy"], picked["room"], picked["score"],
             picked["switched"]))
    print("    reason: %s" % picked["reason"])
    for room, info in sorted(mesh.stats(site)["rooms"].items()):
        print("    room %s: avg_health=%.3f best_rtt=%s cooling=%s" % (
            room, info["avg_health"],
            "-" if info["best_rtt"] is None else "%.2fs" % info["best_rtt"],
            info["cooling_room"]))
    return picked


clock = Clock()
mesh = Mesh(clock=clock)
mesh.set_pool("shop", [
    {"proxy": "bj-1", "room": "beijing"},
    {"proxy": "bj-2", "room": "beijing"},
    {"proxy": "sh-1", "room": "shanghai"},
])
site = "shop"

print("=== 1. 建立延迟基线：选延迟更低的北京 ===")
for proxy, rtt in (("bj-1", 0.10), ("bj-2", 0.12), ("sh-1", 0.35)):
    mesh.report(proxy, True, rtt)
show(mesh, site, "延迟基线")

print("\n=== 2. 单出口连续失败：先降权，再冷却切走 ===")
for i in range(2):
    result = mesh.report("bj-1", False, 5.0)
    print("report bj-1 失败 #%d: health=%.3f cooling=%s switched=%s -> %s"
          % (i + 1, result["health_score"], result["cooling"],
             result["switched"], result["selected"]))
show(mesh, site, "bj-1 冷却后")

print("\n=== 3. 机房级故障：北京整体切到上海 ===")
for i in range(3):
    result = mesh.report("bj-2", False, 5.0)
    print("report bj-2 失败 #%d: health=%.3f room_cooling=%s switched=%s -> %s"
          % (i + 1, result["health_score"], result["room_cooling"],
             result["switched"], result["selected"]))
show(mesh, site, "北京机房整体故障")

print("\n=== 4. 选路日志保留候选与原因 ===")
for entry in mesh.selection_log(site, limit=3):
    candidates = ", ".join(
        "%s(%s)" % (c["proxy"], "冷却" if not c["eligible"]
                    else "%.3f" % c["score"])
        for c in entry["candidates"])
    print("- %s: %s | %s" % (entry["reason_code"], entry["proxy"], entry["reason"]))
    print("    candidates: %s" % candidates)

print("\n=== 5. 等待冷却到期：健康分随时间衰减回填 ===")
clock.advance(31)
print("31s 后北京机房: %s" % {
    k: round(v["avg_health"], 3)
    for k, v in mesh.stats(site)["rooms"].items()})
clock.advance(30)
show(mesh, site, "北京机房恢复参与选路")

print("\n=== 6. 连续成功探测：健康分逐步回量，切回低延迟机房 ===")
for i in range(8):
    score = mesh.report("bj-1", True, 0.10)["health_score"]
    print("  bj-1 第 %d 次成功探测: health=%.3f" % (i + 1, score))
show(mesh, site, "北京回量后")

print("\n=== 7. 手动锁定机房：自动切换只在该机房内 ===")
mesh.lock_room(site, "beijing")
show(mesh, site, "锁定北京")
mesh.unlock_room(site)
show(mesh, site, "解除锁定")
