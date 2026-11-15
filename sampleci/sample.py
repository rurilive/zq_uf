"""可复现的分层采样。

设计要点：
- 种子固定：同一份数据、同一个计划（plan），两次结果逐行一致；
  每个层用 ``hash(seed, 层名)`` 派生独立 Random，新增/删除其他层不会
  改变本层抽到的索引。
- 分层：按 ``strata_field`` 归组；比例（ratio）和固定条数（n）可按层混用，
  采样量超过层大小（含 tail 阈值）时自动普查（census）。
- 重放：:func:`replay` 只依赖计划里保存的层名与索引，不重新抽随机数，
  因此与 :func:`sample` 的随机实现解耦，任何时候都能精确复现。
"""
from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence

DEFAULT_SEED = 20261116

# 计划中可被 plan 参数覆盖的默认值
_DEFAULTS = {
    "seed": DEFAULT_SEED,
    "strata_field": None,          # None / 缺省 => 单一大层
    "ratio": 0.1,                  # 全局兜底比例
    "n": None,                     # 全局兜底固定条数（优先于 ratio）
    "strata": {},                  # {层名: {"ratio": x} 或 {"n": k}}
    "tail_field": None,            # 长尾判定字段（缺省取 value_field）
    "tail_quantile": 0.99,         # 该字段分位 >= 此值 => 长尾层
    "tail_min_count": 30,          # 或层内条数 <= 此值 => 长尾层
    "tail_census": True,           # 长尾层是否自动普查
    "value_field": "value",
}


def _derived_seed(seed: int, stratum: str) -> int:
    """由全局种子与层名派生 64 位层种子（跨进程、跨 Python 版本稳定）。"""
    digest = hashlib.sha256(f"{seed}|{stratum}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def normalize_plan(plan: Optional[dict]) -> dict:
    """补齐默认值，返回 JSON 可序列化的规范化计划。"""
    plan = dict(plan or {})
    out: Dict[str, Any] = {}
    for key, default in _DEFAULTS.items():
        out[key] = default
        if key in plan and plan[key] is not None:
            out[key] = plan[key]
    # strata 子表逐份拷贝，避免持有调用方对象
    out["strata"] = {str(k): dict(v) for k, v in (out["strata"] or {}).items()}
    if out["strata_field"] is not None:
        out["strata_field"] = str(out["strata_field"])
    out["tail_field"] = out["tail_field"] or out["value_field"]
    out["seed"] = int(out["seed"])
    out["ratio"] = float(out["ratio"])
    if not 0.0 <= out["ratio"] <= 1.0:
        raise ValueError("ratio 必须落在 [0, 1]")
    if out["n"] is not None and int(out["n"]) < 0:
        raise ValueError("n 不能为负数")
    return out


def _stratum_label(row: Any, field: Optional[str]) -> str:
    if not isinstance(row, dict) or field is None:
        return "_all"
    value = row.get(field)
    if value is None:
        return "_null"
    return str(value)


def _resolve_strata(rows: Sequence[dict], plan: dict):
    """归组并返回 (groups, ordered_labels, tail_labels)。

    groups: {层名: [行在原数据中的索引...]}，索引保持原始相对顺序。
    """
    field = plan["strata_field"]
    groups: Dict[str, List[int]] = {}
    order: List[str] = []
    for idx, row in enumerate(rows):
        label = _stratum_label(row, field)
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(idx)

    # 长尾是“层”的概念：未分层（单一大层）时不做长尾判定，
    # 否则唯一的层会被自身的极端值强制普查，比例采样形同虚设。
    if field is None:
        return groups, order, set()

    tail_field = plan["tail_field"]
    tail_labels = set()
    threshold_n = int(plan["tail_min_count"])

    def numeric(i):
        row = rows[i]
        if not isinstance(row, dict):
            return None
        v = row.get(tail_field)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    global_vals = sorted(v for i in range(len(rows))
                         if (v := numeric(i)) is not None)
    global_cutoff = _interp_quantile(global_vals, float(plan["tail_quantile"])) \
        if global_vals else None

    for label, indices in groups.items():
        # 规则一：稀有小层（条数 <= tail_min_count）
        if len(indices) <= threshold_n:
            tail_labels.add(label)
            continue
        # 规则二：高值层——层均值落在全局 P99 阈值之上，
        # 说明该层整体由长尾大值构成（而非仅混入个别极端点）
        if global_cutoff is not None:
            vals = [v for i in indices if (v := numeric(i)) is not None]
            if vals and sum(vals) / len(vals) >= global_cutoff:
                tail_labels.add(label)
    return groups, order, tail_labels


def _interp_quantile(sorted_values: Sequence[float], prob: float) -> float:
    """线性插值分位数（R-7 约定，h = (n-1)*p），入参必须已排序。"""
    if not sorted_values:
        raise ValueError("空序列无法计算分位数")
    if prob <= 0:
        return float(sorted_values[0])
    if prob >= 1:
        return float(sorted_values[-1])
    pos = prob * (len(sorted_values) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_values):
        return float(sorted_values[-1])
    return sorted_values[lo] * (1.0 - frac) + sorted_values[lo + 1] * frac


def _allocate(label: str, size: int, plan: dict, is_tail: bool) -> (int, str):
    """决定某层抽样条数，返回 (n, mode)；mode 为 census/ratio/n。"""
    if is_tail and plan["tail_census"]:
        return size, "census"
    spec = plan["strata"].get(label, {})
    if "n" in spec and spec["n"] is not None:
        n = int(spec["n"])
        mode = "n"
    elif "ratio" in spec and spec["ratio"] is not None:
        n = int(round(float(spec["ratio"]) * size))
        mode = "ratio"
    elif plan["n"] is not None:
        n = int(plan["n"])
        mode = "n"
    else:
        n = int(round(plan["ratio"] * size))
        mode = "ratio"
    if n >= size:
        return size, "census"
    return max(n, 0), mode


def sample(rows: Iterable[dict], plan: Optional[dict] = None) -> dict:
    """按计划执行可复现分层采样。

    返回结构::

        {
          "rows": [...],            # 抽到的行（保持原数据相对顺序）
          "indices": [...],         # 行在输入数据中的全局索引（重放用）
          "seed": 20261116,         # 实际使用的种子
          "plan": {...},            # 规范化后的完整计划
          "n_total": 1000, "n_sampled": 120,
          "strata": {               # 分层细节
             label: {"size": N, "sampled": n, "mode": "ratio|n|census",
                     "tail": bool, "indices": [...]}, ...
          },
          "tail_labels": [...],
        }
    """
    materialized = list(rows)
    p = normalize_plan(plan)
    groups, order, tail_labels = _resolve_strata(materialized, p)

    strata_out: Dict[str, dict] = {}
    all_picked: List[int] = []
    for label in order:
        indices = groups[label]
        is_tail = label in tail_labels
        n_h, mode = _allocate(label, len(indices), p, is_tail)
        if mode == "census" or n_h == len(indices):
            picked = list(indices)
        else:
            rng = random.Random(_derived_seed(p["seed"], label))
            picked = sorted(rng.sample(indices, n_h))
        strata_out[label] = {
            "size": len(indices),
            "sampled": len(picked),
            "mode": "census" if len(picked) == len(indices) else mode,
            "tail": is_tail,
            "indices": picked,
        }
        all_picked.extend(picked)

    all_picked.sort()
    return {
        "rows": [materialized[i] for i in all_picked],
        "indices": all_picked,
        "seed": p["seed"],
        "plan": p,
        "n_total": len(materialized),
        "n_sampled": len(all_picked),
        "strata": strata_out,
        "tail_labels": sorted(tail_labels),
    }


def replay(rows: Iterable[dict], plan: dict, recorded: Optional[dict] = None) -> dict:
    """按同一计划重放一次采样。

    两种用法：
    - ``replay(rows, plan)``：用固定种子重新执行 :func:`sample`，
      数据不变则结果逐行一致；
    - ``replay(rows, plan, recorded)``：直接按上次结果里保存的分层索引取行，
      不重新抽随机数——即使数据中的行顺序变化也能按原始索引精确定位，
      并会校验层数、层大小与计划是否与录制时一致。
    """
    materialized = list(rows)
    p = normalize_plan(plan)

    if recorded is None:
        fresh = sample(materialized, p)
        fresh["replayed"] = False
        return fresh

    rec_plan = normalize_plan(recorded.get("plan", p))
    if rec_plan != p:
        raise ValueError("重放计划与录制计划不一致，拒绝重放")
    rec_strata = recorded.get("strata", {})

    groups, order, tail_labels = _resolve_strata(materialized, p)
    if set(order) != set(rec_strata):
        raise ValueError(
            f"层集合不一致：当前 {sorted(order)} vs 录制 {sorted(rec_strata)}"
        )

    strata_out: Dict[str, dict] = {}
    all_picked: List[int] = []
    for label in order:
        rec = rec_strata[label]
        if rec["size"] != len(groups[label]):
            raise ValueError(
                f"层 {label!r} 大小变化：当前 {len(groups[label])} "
                f"vs 录制 {rec['size']}"
            )
        wanted = list(rec["indices"])
        current = set(groups[label])
        if any(i not in current for i in wanted):
            raise ValueError(f"层 {label!r} 的录制索引在当前数据中已不属于该层")
        strata_out[label] = {
            "size": len(groups[label]),
            "sampled": len(wanted),
            "mode": rec.get("mode", "replay"),
            "tail": label in tail_labels,
            "indices": wanted,
        }
        all_picked.extend(wanted)

    all_picked.sort()
    return {
        "rows": [materialized[i] for i in all_picked],
        "indices": all_picked,
        "seed": p["seed"],
        "plan": p,
        "n_total": len(materialized),
        "n_sampled": len(all_picked),
        "strata": strata_out,
        "tail_labels": sorted(tail_labels),
        "replayed": True,
    }
