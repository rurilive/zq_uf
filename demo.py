"""演示带漂移校正的调度时钟。

用可拨动的假时钟模拟：本机每天快若干秒、超过阈值先告警再平滑校正、
夏令时切换重新锚定并给出前后窗口、参考时间不可达时保持上次偏移。
"""
import tempfile
from datetime import datetime, timedelta, timezone

import crawlclock

# 假时钟：起点 2026-03-07T12:00Z（美东 EST），比参考时间快 6 秒
START = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc).timestamp()
FAKE = [START]
DRIFT = 6.0


def fake_clock():
    return FAKE[0]


def reference():
    """参考时间：真实时间。模拟本机偏快 6 秒。"""
    return FAKE[0] - DRIFT


def advance(seconds):
    FAKE[0] += seconds


def show(label, result):
    print(
        f"{label:<22} drift={result['drift']:+.3f}s "
        f"offset={result['offset']:+.3f}s -> target={result['target_offset']:+.3f}s "
        f"alert={result['alerted']} slewing={result['slewing']} "
        f"dst={bool(result['dst'])}"
    )


with tempfile.TemporaryDirectory() as tmp:
    log_path = f"{tmp}/drift.jsonl"
    crawlclock.configure(
        station="America/New_York",
        machine_id="crawler-a",
        clock=fake_clock,
        drift_threshold=1.0,   # 超过 1 秒先告警
        slew_rate=0.05,        # 每秒最多校正 0.05 秒
        log_path=log_path,
        reset=True,
    )

    print("== 第一段：漂移超过阈值，先告警再平滑校正 ==")
    result = crawlclock.sync(reference)
    show("sync #1", result)
    print(f"  调度时间 now() = {crawlclock.now().isoformat()}")

    # 每秒同步一次，观察偏移平滑逼近
    for step in range(1, 5):
        advance(30)
        show(f"after {step * 30}s", crawlclock.sync(reference))
    advance(200)
    show("after +200s", crawlclock.sync(reference))
    print(f"  校正后 now() = {crawlclock.now().isoformat()}")

    print("\n== 第二段：夏令时切换（2026-03-08 美东 02:00 -> 03:00）==")
    fake_to = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc).timestamp()
    advance(fake_to - FAKE[0])
    result = crawlclock.sync(reference)
    dst = result["dst"]
    print(f"  站点时区: {result['timezone']}")
    print(f"  切换方向: {dst['direction']}，UTC 切换点 {dst['transition_utc']}")
    print(f"  本地: {dst['local_before']} -> {dst['local_after']}")
    print(f"  偏移: {dst['offset_before_seconds']}s -> {dst['offset_after_seconds']}s")
    print(f"  前窗口: {dst['windows']['before']['start_local']} ~ "
          f"{dst['windows']['before']['end_local']}")
    print(f"  后窗口: {dst['windows']['after']['start_local']} ~ "
          f"{dst['windows']['after']['end_local']}")
    if "gap" in dst:
        print(f"  缺口:   {dst['gap']['start_local']} ~ {dst['gap']['end_local']}")
    print(f"  切换后 now() = {crawlclock.now().isoformat()}"
          f"（utcoffset={crawlclock.now().utcoffset()}）")

    print("\n== 第三段：跨时区任务统一按站点时区计算窗口 ==")
    window = crawlclock.station_window(
        "2026-03-09T09:00:00+08:00", "2026-03-09T10:30:00+08:00"
    )
    print(f"  输入: 上海时间 09:00 ~ 10:30")
    print(f"  站点窗口: {window['start'].isoformat()} ~ {window['end'].isoformat()}")
    print(f"  时长: {window['seconds']}s")

    print("\n== 第四段：参考时间不可达，保持上次偏移 ==")
    before = crawlclock.current_offset()
    result = crawlclock.sync(None)
    show("reference=None", result)
    print(f"  偏移保持: {before:+.6f}s -> {crawlclock.current_offset():+.6f}s "
          f"reachable={result['reachable']}")

    print("\n== 第五段：按机器查看漂移曲线和记录 ==")
    curve = crawlclock.drift_curve(machine="crawler-a")
    print(f"  漂移曲线 {len(curve)} 点:")
    for point in curve:
        print(f"    {point['at']}  drift={point['drift']:+7.3f}s  "
              f"offset={point['offset']:+7.3f}s")

    print("\n  记录事件:")
    for record in crawlclock.records(machine="crawler-a")[:8]:
        print(f"    seq={record['seq']:<3} {record['event']:<12} "
              f"{record.get('at', '')}")
    print(f"    ... 共 {len(crawlclock.records())} 条，"
          f"已写入 {log_path}")

    print("\n  从文件重新读取漂移曲线:")
    loaded = crawlclock.load_records(log_path)
    print(f"    读到 {len(loaded)} 条记录，"
          f"漂移点 {len(crawlclock.drift_curve(machine='crawler-a', records=loaded))} 个")
