"""带漂移校正的采集调度时钟。

纯标准库实现。``now()`` 返回站点时区下的调度时间；``sync(reference)``
用参考时间估计漂移，并以不超过 ``slew_rate`` 的速度平滑校正。校正后的
时间单调不回退，夏令时切换只影响站点时区显示，不影响漂移估计。

口径
----
``raw_drift`` = 未校正的本地时钟 - 参考时间，正值表示本机偏快。
``drift`` = 调度实际看到的偏差 = ``(本地 + offset) - 参考时间``。
``offset`` 是平滑校正量，``now() = 本地 + offset``；``target_offset``
由 ``sync`` 计算，通常为 ``-raw_drift``，校正完成后 ``drift`` 归零。
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Iterable

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - 仅用于极老的解释器
    ZoneInfo = None

_LOG = logging.getLogger("crawlclock")
_EPS = 1e-9
_DEFAULT_THRESHOLD = 1.0
_DEFAULT_SLEW_RATE = 0.05
_DEFAULT_DEADBAND = 0.01
# 检测夏令时切换时最多向前回溯的跨度，避免时钟大幅跳变时长时间扫描
_MAX_DST_SCAN_SECONDS = 400 * 24 * 3600.0
# 端点偏移相同且跨度小于该值时跳过扫描（一次切换必然改变偏移，短跨度可安全跳过）
_DST_SAME_OFFSET_SCAN_SECONDS = 90 * 24 * 3600.0


class ReferenceUnavailable(Exception):
    """参考时间不可达。``sync`` 捕获后保持上次偏移。"""


# --------------------------------------------------------------------------
# 时区探测
# --------------------------------------------------------------------------
def _default_station() -> tzinfo:
    """尽力取本机 IANA 时区；取不到时退回固定偏移时区。"""
    candidates = []
    tz_env = os.environ.get("TZ")
    if tz_env:
        candidates.append(tz_env.lstrip(":"))

    try:
        with open("/etc/timezone", encoding="utf-8") as fh:
            candidates.append(fh.read().strip())
    except OSError:
        pass

    try:
        link = os.path.realpath("/etc/localtime")
        marker = "zoneinfo/"
        if marker in link:
            candidates.append(link.split(marker, 1)[1])
    except OSError:
        pass

    if ZoneInfo is not None:
        for name in candidates:
            if not name or name.startswith((":", "posix/", "right/")):
                continue
            try:
                return ZoneInfo(name)
            except Exception:
                continue

    return datetime.now().astimezone().tzinfo or timezone.utc


def _zone_name(zone: tzinfo) -> str:
    key = getattr(zone, "key", None)
    if key:
        return key
    name = zone.tzname(None)
    return name or str(zone)


def _offset_at(epoch: float, zone: tzinfo) -> float:
    delta = datetime.fromtimestamp(epoch, zone).utcoffset()
    return delta.total_seconds() if delta is not None else 0.0


def _iso(epoch: float, zone: tzinfo) -> str:
    return datetime.fromtimestamp(epoch, zone).isoformat()


def _host_name() -> str:
    return socket.gethostname() or "unknown"


# --------------------------------------------------------------------------
# 模块状态
# --------------------------------------------------------------------------
_lock = threading.RLock()
_clock: Callable[[], float] = time.time
_station: tzinfo = _default_station()
_machine: str = _host_name()
_drift_threshold: float = _DEFAULT_THRESHOLD
_slew_rate: float = _DEFAULT_SLEW_RATE
_deadband: float = _DEFAULT_DEADBAND
_log_path: str | None = None

_offset: float = 0.0
_slew_target: float | None = None
_last_base: float = _clock()
_last_epoch: float | None = None
_anchor_utc: float = _last_base
_anchor_offset: float = _offset_at(_last_base, _station)
_last_drift_value: float | None = None
_last_raw_drift: float | None = None
_alert_active: bool = False

_records: list[dict[str, Any]] = []
_seq: int = 0
_dst_pending: dict[str, Any] | None = None
_last_dst: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------
def _record(event: str, at_epoch: float, **fields: Any) -> dict[str, Any]:
    """写一条记录：内存 + 可选 JSONL 文件。"""
    global _seq
    _seq += 1
    record: dict[str, Any] = {
        "seq": _seq,
        "event": event,
        "machine": _machine,
        "timezone": _zone_name(_station),
        "at": _iso(at_epoch, _station),
        "epoch": at_epoch,
    }
    record.update(fields)
    _records.append(record)

    path = _log_path
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:  # 记录失败不能拖垮调度
            _LOG.warning("crawlclock 无法写入记录文件 %s: %s", path, exc)
    return record


def _advance(base: float) -> None:
    """按真实流逝时间推进平滑校正，速率不超过 slew_rate。"""
    global _offset, _slew_target, _last_base
    elapsed = base - _last_base
    if elapsed < 0:
        elapsed = 0.0
    _last_base = base
    if _slew_target is None:
        return
    diff = _slew_target - _offset
    step = _slew_rate * elapsed
    if abs(diff) <= step:
        _offset = _slew_target
        _slew_target = None
    elif step > 0:
        _offset += math.copysign(step, diff)


def _find_transitions(lo: float, hi: float) -> list[tuple[float, float, float]]:
    """在 [lo, hi] 中定位所有 UTC 偏移变化点。

    返回 ``[(切换时刻, 切换前偏移, 切换后偏移), ...]``，精度到秒，
    按时间升序。
    """
    step = 6 * 3600.0
    cur = lo
    cur_offset = _offset_at(cur, _station)
    found: list[tuple[float, float, float]] = []
    while cur < hi:
        nxt = min(cur + step, hi)
        nxt_offset = _offset_at(nxt, _station)
        if nxt_offset != cur_offset:
            left, right = cur, nxt
            base_offset = _offset_at(left, _station)
            while right - left > 1e-3:
                mid = (left + right) / 2.0
                if _offset_at(mid, _station) == base_offset:
                    left = mid
                else:
                    right = mid
            right = _snap_to_second(right, base_offset)
            found.append((right, cur_offset, _offset_at(right, _station)))
            # 保证向前推进，避免极端吸附结果造成死循环
            next_cur = max(right, cur + 1e-3)
            cur, cur_offset = next_cur, _offset_at(next_cur, _station)
        else:
            cur, cur_offset = nxt, nxt_offset
    return found


def _snap_to_second(transition: float, old_offset: float) -> float:
    """时区切换通常落在整秒；把二分结果吸附到整秒以减少微秒抖动。"""
    candidate = float(round(transition))
    if (
        _offset_at(candidate - 1.0, _station) == old_offset
        and _offset_at(candidate, _station) != old_offset
    ):
        return candidate
    return transition


def _dst_info(transition: float, before_offset: float, after_offset: float) -> dict[str, Any]:
    """把一次时区偏移变化整理成带前后窗口的描述。"""
    direction = "forward" if after_offset > before_offset else "backward"
    shift = abs(after_offset - before_offset)
    before_instant = transition - 1e-6
    local_before = datetime.fromtimestamp(before_instant, _station)
    local_after = datetime.fromtimestamp(transition, _station)
    window_before_start = transition - shift

    dst: dict[str, Any] = {
        "changed": True,
        "direction": direction,
        "offset_before_seconds": before_offset,
        "offset_after_seconds": after_offset,
        "transition_utc": _iso(transition, timezone.utc),
        "transition_epoch": transition,
        "local_before": local_before.isoformat(),
        "local_after": local_after.isoformat(),
        "windows": {
            "before": {
                "start_utc": _iso(window_before_start, timezone.utc),
                "end_utc": _iso(before_instant, timezone.utc),
                "start_local": _iso(window_before_start, _station),
                "end_local": local_before.isoformat(),
                "seconds": shift,
            },
            "after": {
                "start_utc": _iso(transition, timezone.utc),
                "end_utc": _iso(transition + shift, timezone.utc),
                "start_local": local_after.isoformat(),
                "end_local": _iso(transition + shift, _station),
                "seconds": shift,
            },
        },
    }
    if direction == "forward":
        dst["gap"] = {
            "start_local": (local_before + timedelta(microseconds=1)).isoformat(),
            "end_local": local_after.isoformat(),
            "seconds": shift,
        }
    else:
        dst["overlap"] = {
            "start_local": local_after.isoformat(),
            "end_local": (local_before + timedelta(microseconds=1)).isoformat(),
            "seconds": shift,
        }
    return dst


def _check_dst(base: float) -> dict[str, Any] | None:
    """检测站点时区偏移是否变化；变化则记录并重新锚定。

    返回本次跨过的**最后一次**切换（前后窗口口径），若跨过多次切换，
    额外附上 ``transitions`` 列表，便于离线多日后补全记录。
    """
    global _anchor_utc, _anchor_offset, _dst_pending, _last_dst
    current_offset = _offset_at(base, _station)
    span = abs(base - _anchor_utc)
    if (
        abs(current_offset - _anchor_offset) < _EPS
        and span < _DST_SAME_OFFSET_SCAN_SECONDS
    ):
        # 端点偏移相同且跨度小，区间内不可能有一次切换
        return None

    # 只向前扫描有限跨度，避免时钟被大幅调整后长时间卡顿
    lo = min(_anchor_utc, base)
    hi = max(_anchor_utc, base)
    lo = max(lo, hi - _MAX_DST_SCAN_SECONDS)
    found = _find_transitions(lo, hi)
    if not found:  # 区间内没有切换点，按当前状态重新锚定
        _anchor_utc, _anchor_offset = base, current_offset
        return None

    _anchor_utc, _anchor_offset = base, current_offset
    dst = _dst_info(*found[-1])
    if len(found) > 1:
        dst["transitions"] = [_dst_info(*item) for item in found]
    _last_dst = copy.deepcopy(dst)
    _dst_pending = copy.deepcopy(dst)
    _record(
        "timezone",
        base,
        direction=dst["direction"],
        transition_utc=dst["transition_utc"],
        offset_before_seconds=dst["offset_before_seconds"],
        offset_after_seconds=dst["offset_after_seconds"],
        windows=dst["windows"],
    )
    _LOG.info(
        "crawlclock 站点 %s 夏令时切换（%s），重新锚定偏移 %.0fs -> %.0fs",
        _zone_name(_station),
        dst["direction"],
        dst["offset_before_seconds"],
        dst["offset_after_seconds"],
    )
    return dst


def _to_epoch(value: Any) -> float:
    """把参考时间统一换算成 UTC epoch 秒。

    naive datetime 按站点时区解释；带时区的 datetime 直接换算；
    数字视为 UTC epoch；字符串按 ISO-8601 解析。
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_station)
        return value.timestamp()
    if isinstance(value, bool):
        raise TypeError("布尔值不是参考时间")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_station)
        return parsed.timestamp()
    raise TypeError(f"不支持的参考时间类型: {type(value).__name__}")


def _last_or_zero(raw: bool = False) -> float:
    value = _last_raw_drift if raw else _last_drift_value
    return float(value) if value is not None else 0.0


# --------------------------------------------------------------------------
# 公共接口
# --------------------------------------------------------------------------
def now() -> datetime:
    """返回站点时区下、经过漂移校正的当前调度时间。

    返回值带时区信息，且保证同一进程内单调不回退。
    """
    global _last_epoch
    with _lock:
        base = _clock()
        _advance(base)
        candidate = base + _offset
        if _last_epoch is not None and candidate < _last_epoch:
            candidate = _last_epoch  # 校正绝不回退时间
        # 夏令时按调度实际看到的时刻（校正后）检测，和显示口径一致
        _check_dst(candidate)
        _last_epoch = candidate
        return datetime.fromtimestamp(candidate, _station)


def sync(reference: Any) -> dict[str, Any]:
    """按参考时间估计漂移并平滑校正。

    参数
    ----
    reference:
        参考时间。支持带时区/不带时区的 ``datetime``、UTC epoch
        数字、ISO-8601 字符串，或返回上述值的可调用对象。
        传入 ``None``、可调用对象抛错或值无法解析时视为参考时间
        不可达：保持上次偏移不变，只写一条 ``unreachable`` 记录。

    返回
    ----
    dict，包含 ``drift``、``corrected``、``timezone``、``machine``、
    ``offset``、``target_offset``、``alerted``、``reachable``、
    ``slewing``、``dst`` 等字段。
    """
    global _slew_target, _last_drift_value, _last_raw_drift, _dst_pending, _alert_active

    with _lock:
        base = _clock()
        _advance(base)

        dst = _check_dst(base + _offset)
        if dst is None and _dst_pending is not None:
            dst = _dst_pending
            _dst_pending = None
        elif dst is not None:
            _dst_pending = None

        # 1) 取参考时间；不可达时保持上次偏移
        try:
            if reference is None:
                raise ReferenceUnavailable("参考时间为 None")
            if callable(reference):
                reference = reference()
                if reference is None:
                    raise ReferenceUnavailable("参考时间源返回 None")
            reference_epoch = _to_epoch(reference)
        except Exception as exc:
            _record(
                "unreachable",
                base,
                reason=type(exc).__name__,
                detail=str(exc),
                drift=_last_or_zero(),
                offset=_offset,
            )
            _LOG.warning(
                "crawlclock 参考时间不可达（%s: %s），保持上次偏移 %.6fs",
                type(exc).__name__,
                exc,
                _offset,
            )
            return {
                "drift": _last_or_zero(),
                "corrected": False,
                "timezone": _zone_name(_station),
                "machine": _machine,
                "offset": _offset,
                "target_offset": _offset,
                "raw_drift": _last_or_zero(raw=True),
                "alerted": False,
                "reachable": False,
                "slewing": _slew_target is not None,
                "dst": dst,
                "at": _iso(base, _station),
            }

        # 2) 算漂移。
        # raw_drift = 未校正的本地时钟误差；drift = 校正后时钟相对参考的偏差，
        # 也就是调度实际看到的偏差。target_offset = -raw_drift 会把 drift 归零。
        raw_drift = base - reference_epoch
        drift = (base + _offset) - reference_epoch
        target_offset = _offset if abs(drift) <= _deadband else -raw_drift
        reported = 0.0 if abs(drift) <= _deadband else drift
        alerted = abs(drift) > _drift_threshold

        _record(
            "sync",
            base,
            reference_epoch=reference_epoch,
            drift=drift,
            raw_drift=raw_drift,
            reported_drift=reported,
            offset=_offset,
            target_offset=target_offset,
            reachable=True,
        )

        # 3) 超过阈值先告警，再开始平滑校正。
        # 告警按状态沿触发：跨过阈值时记一次，回落到阈值内解除，避免刷屏。
        if alerted and not _alert_active:
            _alert_active = True
            _record(
                "alert",
                base,
                drift=drift,
                raw_drift=raw_drift,
                threshold=_drift_threshold,
                offset=_offset,
                target_offset=target_offset,
            )
            _LOG.warning(
                "crawlclock 漂移 %.6fs（原始 %.6fs）超过阈值 %.6fs（机器 %s，站点 %s），"
                "先告警，随后以 %.4g/s 平滑校正",
                drift,
                raw_drift,
                _drift_threshold,
                _machine,
                _zone_name(_station),
                _slew_rate,
            )
        elif not alerted and _alert_active:
            _alert_active = False
            _record("alert_cleared", base, drift=drift, threshold=_drift_threshold)
            _LOG.info(
                "crawlclock 漂移回落到阈值内（%.6fs <= %.6fs），解除告警",
                drift,
                _drift_threshold,
            )

        corrected = False
        if abs(target_offset - _offset) <= _EPS:
            _slew_target = None
        else:
            is_new = _slew_target is None or abs(target_offset - _slew_target) > _EPS
            _slew_target = target_offset
            corrected = is_new
            if is_new:
                _record(
                    "correction",
                    base,
                    from_offset=_offset,
                    target_offset=target_offset,
                    delta=target_offset - _offset,
                    slew_rate=_slew_rate,
                    drift=drift,
                    alerted=alerted,
                )

        _last_drift_value = drift
        _last_raw_drift = raw_drift
        return {
            "drift": reported,
            "corrected": corrected,
            "timezone": _zone_name(_station),
            "machine": _machine,
            "offset": _offset,
            "target_offset": target_offset,
            "raw_drift": raw_drift,
            "alerted": alerted,
            "reachable": True,
            "slewing": _slew_target is not None,
            "dst": dst,
            "at": _iso(base, _station),
        }


def configure(
    *,
    station: Any = None,
    machine_id: str | None = None,
    drift_threshold: float = _DEFAULT_THRESHOLD,
    slew_rate: float = _DEFAULT_SLEW_RATE,
    deadband: float = _DEFAULT_DEADBAND,
    log_path: str | os.PathLike[str] | None = None,
    clock: Callable[[], float] | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """配置站点时区、机器标识、阈值和记录文件。

    ``station`` 可为 IANA 名称字符串或 ``tzinfo``；``clock`` 供测试/演示
    注入返回 UTC epoch 秒的时钟。``reset=True`` 会先清空偏移与记录。
    """
    global _station, _machine, _drift_threshold, _slew_rate, _deadband
    global _log_path, _clock, _last_base, _anchor_utc, _anchor_offset, _last_epoch

    if reset:
        reset_state()

    if station is None:
        zone = _default_station()
    elif isinstance(station, str):
        if ZoneInfo is None:
            raise ValueError("当前解释器不支持 zoneinfo")
        try:
            zone = ZoneInfo(station)
        except Exception as exc:
            raise ValueError(f"无法识别站点时区: {station}") from exc
    elif isinstance(station, tzinfo):
        zone = station
    else:
        raise TypeError("station 必须是 IANA 名称字符串或 tzinfo")

    if not (drift_threshold > 0) or not math.isfinite(drift_threshold):
        raise ValueError("drift_threshold 必须是有限正数")
    if not (0 < slew_rate < 1):
        raise ValueError("slew_rate 必须在 (0, 1) 之间，否则校正可能回退时间")
    if deadband < 0 or not math.isfinite(deadband):
        raise ValueError("deadband 必须是非负有限数")
    if clock is not None and not callable(clock):
        raise TypeError("clock 必须是返回 epoch 秒的可调用对象")

    if log_path is not None:
        log_path = os.fspath(log_path)
        parent = os.path.dirname(os.path.abspath(log_path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    with _lock:
        _station = zone
        _machine = machine_id if machine_id else _host_name()
        _drift_threshold = float(drift_threshold)
        _slew_rate = float(slew_rate)
        _deadband = float(deadband)
        _log_path = log_path
        if clock is not None:
            _clock = clock
        _last_base = _clock()
        _anchor_utc = _last_base
        _anchor_offset = _offset_at(_last_base, _station)
        if reset:
            _last_epoch = None

        settings = {
            "station": _zone_name(_station),
            "machine": _machine,
            "drift_threshold": _drift_threshold,
            "slew_rate": _slew_rate,
            "deadband": _deadband,
            "log_path": _log_path,
            "offset": _offset,
        }
        _record(
            "configure",
            _last_base,
            station=_zone_name(_station),
            settings={
                "drift_threshold": _drift_threshold,
                "slew_rate": _slew_rate,
                "deadband": _deadband,
                "log_path": _log_path,
            },
        )
        return dict(settings)


def reset_state() -> None:
    """恢复默认配置并清空偏移、记录和锚点。"""
    global _clock, _station, _machine, _drift_threshold, _slew_rate, _deadband
    global _log_path, _offset, _slew_target, _last_base, _last_epoch
    global _anchor_utc, _anchor_offset, _last_drift_value, _last_raw_drift, _alert_active
    global _records, _seq, _dst_pending, _last_dst

    with _lock:
        _clock = time.time
        _station = _default_station()
        _machine = _host_name()
        _drift_threshold = _DEFAULT_THRESHOLD
        _slew_rate = _DEFAULT_SLEW_RATE
        _deadband = _DEFAULT_DEADBAND
        _log_path = None
        _offset = 0.0
        _slew_target = None
        _last_base = _clock()
        _last_epoch = None
        _anchor_utc = _last_base
        _anchor_offset = _offset_at(_last_base, _station)
        _last_drift_value = None
        _last_raw_drift = None
        _alert_active = False
        _records = []
        _seq = 0
        _dst_pending = None
        _last_dst = None


def records(
    machine: str | None = None, event: str | None = None
) -> list[dict[str, Any]]:
    """按机器/事件类型查看记录（深拷贝，按写入顺序）。"""
    with _lock:
        result = []
        for record in _records:
            if machine is not None and record.get("machine") != machine:
                continue
            if event is not None and record.get("event") != event:
                continue
            result.append(copy.deepcopy(record))
        return result


def drift_curve(
    machine: str | None = None, records: Iterable[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """按机器输出漂移曲线点（来自成功 sync 记录，按时间排序）。"""
    with _lock:
        source = list(_records) if records is None else list(records)
        target = machine if machine is not None else _machine
    points = []
    for record in source:
        if record.get("machine") != target:
            continue
        if record.get("event") != "sync" or not record.get("reachable", False):
            continue
        points.append(
            {
                "machine": record.get("machine"),
                "at": record.get("at"),
                "epoch": record.get("epoch"),
                "drift": record.get("drift"),
                "raw_drift": record.get("raw_drift"),
                "offset": record.get("offset"),
                "target_offset": record.get("target_offset"),
            }
        )
    points.sort(key=lambda point: point.get("epoch") or 0.0)
    return points


def load_records(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """读取 JSONL 记录文件，坏行跳过。"""
    loaded = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                loaded.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return loaded


def station_timezone() -> tzinfo:
    """当前站点时区。"""
    with _lock:
        return _station


def station_name() -> str:
    """当前站点时区名。"""
    with _lock:
        return _zone_name(_station)


def machine_id() -> str:
    """当前机器标识。"""
    with _lock:
        return _machine


def to_station(value: Any) -> datetime:
    """把任意参考时间换算成站点时区的 aware datetime。"""
    with _lock:
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(_station)
        return datetime.fromtimestamp(_to_epoch(value), _station)


def station_window(start: Any, end: Any) -> dict[str, Any]:
    """把调度窗口统一换算到站点时区，跨时区任务按站点口径计算。"""
    start_dt = to_station(start)
    end_dt = to_station(end)
    with _lock:
        return {
            "timezone": _zone_name(_station),
            "start": start_dt,
            "end": end_dt,
            "seconds": (end_dt - start_dt).total_seconds(),
            "start_utc": start_dt.astimezone(timezone.utc).isoformat(),
            "end_utc": end_dt.astimezone(timezone.utc).isoformat(),
        }


def last_dst() -> dict[str, Any] | None:
    """最近一次检测到的夏令时切换信息。"""
    with _lock:
        return copy.deepcopy(_last_dst)


def current_offset() -> float:
    """当前生效的平滑校正偏移（秒），按最新时钟推进后再取值。"""
    with _lock:
        _advance(_clock())
        return _offset


def status() -> dict[str, Any]:
    """当前时钟状态快照：站点、机器、偏移、漂移和最近夏令时切换。"""
    with _lock:
        _advance(_clock())
        return {
            "timezone": _zone_name(_station),
            "machine": _machine,
            "offset": _offset,
            "target_offset": _slew_target,
            "slewing": _slew_target is not None,
            "last_drift": _last_drift_value,
            "alert_active": _alert_active,
            "drift_threshold": _drift_threshold,
            "slew_rate": _slew_rate,
            "deadband": _deadband,
            "log_path": _log_path,
            "last_dst": copy.deepcopy(_last_dst),
            "records": len(_records),
        }
