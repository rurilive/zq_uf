"""批量修补的状态引擎：留痕、统计、告警、回滚、回放、导出。

设计要点：
- 修补以“批次”为单位落库，每条留痕都带 batch_id / site / day，可按批次回滚。
- 正常行进 records（按 record_id 保存当前状态），隔离行进 quarantine。
- 统计直接由活跃批次（未回滚）的留痕动态计算，回滚后数字自动一致。
"""

import csv
import io
import uuid
from collections import defaultdict

from .patch import patch

# 批次状态
ACTIVE = "active"
ROLLED_BACK = "rolled_back"


class Store:
    def __init__(self, missing_threshold=0.3):
        self.missing_threshold = missing_threshold
        self.reset()

    def reset(self):
        """清空全部状态（测试/演示用）。"""
        self.batches = {}          # batch_id -> 批次元数据
        self.audits = []           # 修补留痕（仅活跃批次计入统计）
        self.quarantine = []       # 隔离记录
        self.records = {}          # record_id -> 当前行
        self.alerts = []           # 缺失率超阈值告警

    # ------------------------------------------------------------------ #
    # 批次修补
    # ------------------------------------------------------------------ #
    def run_batch(self, rows, schema, site=None, day=None, dry_run=False):
        """对一批记录执行按字段修补。

        同一批里字段齐全的记录不会因别的记录缺字段而受影响。
        ``dry_run=True`` 为试运行：只返回结果与统计，不落库、不告警，
        用于先在单个站点确认补齐字段能被下游正常读取。
        """
        batch_id = None if dry_run else uuid.uuid4().hex[:12]
        results, audit, quarantine = [], [], []
        counters = defaultdict(int)
        missing = missing_required = total = 0

        for raw in rows:
            res = patch(raw, schema)
            results.append(res)
            counters["rows"] += 1
            counters["patched"] += len(res["patched"])
            counters["isolated"] += len(res["isolated"])

            for name, info in res["fields"].items():
                total += 1
                if info["reason"] in ("missing", "missing_required"):
                    missing += 1
                if info["reason"] == "missing_required":
                    missing_required += 1

            entry = {
                "batch_id": batch_id,
                "site": site,
                "day": day,
                "record_id": raw.get("id"),
                "field": None,  # 行级汇总占位；逐字段见 field_entries
                "patched": list(res["patched"]),
                "isolated": list(res["isolated"]),
                "sources": dict(res["sources"]),
                "field_entries": [
                    {
                        "field": name,
                        "action": info["action"],
                        "source": info["source"],
                        "reason": info["reason"],
                        "original": info["original"],
                        "value": info["value"],
                    }
                    for name, info in res["fields"].items()
                ],
            }
            audit.append(entry)

            if res["is_isolated"]:
                quarantine.append({
                    "batch_id": batch_id,
                    "site": site,
                    "day": day,
                    "record_id": raw.get("id"),
                    "row": res["row"],
                    "raw_row": dict(raw),
                    "isolated": list(res["isolated"]),
                })

        ratio = missing / total if total else 0.0
        summary = {
            "rows": counters["rows"],
            "patched": counters["patched"],
            "isolated": counters["isolated"],
            "missing": missing,
            "missing_required": missing_required,
            "total_fields": total,
            "missing_ratio": round(ratio, 4),
        }

        if dry_run:
            # 试运行：不落库，返回“如果执行会发生什么”
            return {
                "batch_id": None,
                "dry_run": True,
                "site": site,
                "day": day,
                "summary": summary,
                "results": results,
                "audit": audit,
                "quarantine": quarantine,
            }

        # 正式落库
        self.batches[batch_id] = {
            "batch_id": batch_id,
            "site": site,
            "day": day,
            "status": ACTIVE,
            "summary": dict(summary),
        }
        self.audits.extend(audit)
        self.quarantine.extend(quarantine)
        for raw, res in zip(rows, results):
            if not res["is_isolated"]:
                self.records[raw.get("id")] = res["row"]

        if ratio > self.missing_threshold:
            self.alerts.append({
                "batch_id": batch_id,
                "site": site,
                "day": day,
                "missing_ratio": round(ratio, 4),
                "threshold": self.missing_threshold,
            })

        return {
            "batch_id": batch_id,
            "dry_run": False,
            "site": site,
            "day": day,
            "summary": summary,
            "results": results,
            "alerts": [a for a in self.alerts if a["batch_id"] == batch_id],
        }

    # ------------------------------------------------------------------ #
    # 回滚 / 回放（按批次，互为逆操作）
    # ------------------------------------------------------------------ #
    def rollback(self, batch_id):
        """按批次回滚：恢复被修补的原值，并把该批隔离记录移出隔离区。"""
        batch = self._require_batch(batch_id)
        if batch["status"] != ACTIVE:
            raise ValueError(f"batch {batch_id} 状态为 {batch['status']}，不可回滚")

        restored, dequarantined = [], []
        for entry in self.audits:
            if entry["batch_id"] != batch_id:
                continue
            rid = entry["record_id"]
            if rid in self.records:
                for fe in entry["field_entries"]:
                    # 仅恢复本批实际动过的字段
                    if fe["action"] is None:
                        continue
                    if fe["reason"] in ("missing", "missing_required"):
                        self.records[rid].pop(fe["field"], None)
                    else:
                        self.records[rid][fe["field"]] = fe["original"]
                restored.append(rid)

        before = len(self.quarantine)
        self.quarantine = [q for q in self.quarantine
                           if q["batch_id"] != batch_id]
        dequarantined = before - len(self.quarantine)

        batch["status"] = ROLLED_BACK
        return {"batch_id": batch_id, "restored": sorted(set(restored)),
                "dequarantined": dequarantined}

    def replay(self, batch_id):
        """回放已回滚的批次：依据留痕重新应用修补并恢复隔离。"""
        batch = self._require_batch(batch_id)
        if batch["status"] != ROLLED_BACK:
            raise ValueError(f"batch {batch_id} 状态为 {batch['status']}，不可回放")

        replayed, requarantined = [], []
        for entry in self.audits:
            if entry["batch_id"] != batch_id:
                continue
            rid = entry["record_id"]
            if entry["isolated"]:
                # 从留痕重建隔离记录
                row = {fe["field"]: fe["value"] for fe in entry["field_entries"]
                       if fe["value"] is not None}
                self.quarantine.append({
                    "batch_id": batch_id, "site": entry["site"],
                    "day": entry["day"], "record_id": rid,
                    "row": row, "raw_row": None,
                    "isolated": list(entry["isolated"]),
                })
                requarantined.append(rid)
            else:
                self.records[rid] = {
                    fe["field"]: fe["value"] for fe in entry["field_entries"]
                }
                replayed.append(rid)

        batch["status"] = ACTIVE
        return {"batch_id": batch_id, "replayed": sorted(set(replayed)),
                "requarantined": sorted(set(requarantined))}

    # ------------------------------------------------------------------ #
    # 统计（按天 + 站点，含缺失比例）
    # ------------------------------------------------------------------ #
    def report(self, day=None, site=None):
        """汇总活跃批次的修补/隔离/来源/缺失比例，可按天、站点过滤。"""
        active_ids = {bid for bid, b in self.batches.items()
                      if b["status"] == ACTIVE
                      and (day is None or b["day"] == day)
                      and (site is None or b["site"] == site)}

        patched = isolated = missing = missing_required = total = 0
        source_counts = defaultdict(int)
        by_day_site = defaultdict(lambda: {
            "patched": 0, "isolated": 0, "missing": 0,
            "missing_required": 0, "total_fields": 0,
            "missing_ratio": 0.0, "sources": defaultdict(int),
        })

        for entry in self.audits:
            if entry["batch_id"] not in active_ids:
                continue
            key = (entry["day"], entry["site"])
            agg = by_day_site[key]
            patched += len(entry["patched"])
            agg["patched"] += len(entry["patched"])
            isolated += len(entry["isolated"])
            agg["isolated"] += len(entry["isolated"])
            for fe in entry["field_entries"]:
                total += 1
                agg["total_fields"] += 1
                source_counts[fe["source"]] += 1
                agg["sources"][fe["source"]] += 1
                if fe["reason"] in ("missing", "missing_required"):
                    missing += 1
                    agg["missing"] += 1
                if fe["reason"] == "missing_required":
                    missing_required += 1
                    agg["missing_required"] += 1

        breakdown = {}
        for (d, s), agg in by_day_site.items():
            agg["missing_ratio"] = round(
                agg["missing"] / agg["total_fields"], 4
            ) if agg["total_fields"] else 0.0
            agg["sources"] = dict(agg["sources"])
            breakdown[f"{d}|{s}"] = agg

        return {
            "day": day,
            "site": site,
            "batches": len(active_ids),
            "patched": patched,
            "isolated": isolated,
            "sources": dict(source_counts),
            "missing": missing,
            "missing_required": missing_required,
            "total_fields": total,
            "missing_ratio": round(missing / total, 4) if total else 0.0,
            "by_day_site": breakdown,
            "alerts": [a for a in self.alerts
                       if (day is None or a["day"] == day)
                       and (site is None or a["site"] == site)],
        }

    # ------------------------------------------------------------------ #
    # 导出（值班同学按天/站点查看，可导出）
    # ------------------------------------------------------------------ #
    def export_patches(self, day=None, site=None, batch_id=None, fmt="dict"):
        """导出逐字段修补留痕，支持 dict / csv。"""
        rows = []
        for entry in self.audits:
            if batch_id is not None and entry["batch_id"] != batch_id:
                continue
            if day is not None and entry["day"] != day:
                continue
            if site is not None and entry["site"] != site:
                continue
            for fe in entry["field_entries"]:
                rows.append({
                    "batch_id": entry["batch_id"],
                    "day": entry["day"],
                    "site": entry["site"],
                    "record_id": entry["record_id"],
                    "field": fe["field"],
                    "action": fe["action"] or "none",
                    "source": fe["source"],
                    "reason": fe["reason"] or "",
                    "original": "" if fe["original"] is None else fe["original"],
                    "value": "" if fe["value"] is None else fe["value"],
                })
        if fmt == "dict":
            return rows
        if fmt == "csv":
            buf = io.StringIO()
            cols = ["batch_id", "day", "site", "record_id", "field",
                    "action", "source", "reason", "original", "value"]
            writer = csv.DictWriter(buf, fieldnames=cols)
            writer.writeheader()
            writer.writerows(rows)
            return buf.getvalue()
        raise ValueError(f"未知导出格式: {fmt}")

    # ------------------------------------------------------------------ #
    def _require_batch(self, batch_id):
        if batch_id not in self.batches:
            raise KeyError(f"未知批次: {batch_id}")
        return self.batches[batch_id]
