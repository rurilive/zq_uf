from blockvote import block

clock = [0.0]
block.set_clock(lambda: clock[0])


def tick(seconds):
    clock[0] += seconds


def show(site):
    print(f"  tally={block.tally(site)} state={block.state(site)} allow={block.allow(site)}")


block.set_listener(
    lambda kind, site, payload: print(f"  [event] t={clock[0]:>4.0f}s {site} {kind} {payload}")
)

print("== 场景1：弱信号矛盾，不熔断 ==")
block.observe("news", "redirect")
block.observe("news", "empty")
show("news")

print("== 场景2：403 误判熔断，人工加白恢复，白名单到期恢复判定 ==")
block.observe("shop", "403")
show("shop")
block.whitelist("shop", ttl=120)
show("shop")
tick(121)
print("  白名单到期，重新按信号判定")
show("shop")

print("== 场景3：多信号累计过阈值熔断，低频探针连续成功后恢复 ==")
block.observe("api", "captcha")
block.observe("api", "ratelimit")
show("api")
tick(block.COOLDOWN)
print("  冷却结束，进入观察态，开始低频探针")
for _ in range(block.PROBE_SUCCESS_REQUIRED):
    print("  allow(探针窗口):", block.allow("api"))
    block.observe("api", "200")
    tick(block.PROBE_INTERVAL)
show("api")

print("== 场景4：观察期再遇负面信号，探针失败重新熔断 ==")
block.observe("cdn", "403")
tick(block.COOLDOWN)
block.observe("cdn", "200")
block.observe("cdn", "captcha")
show("cdn")
