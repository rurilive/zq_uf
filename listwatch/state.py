"""按站点落本地的增量运行状态与变更量阈值告警。"""

import json
import os
import tempfile

from .diff import diff, merge_pages, stable_key


class ChangeThresholdExceeded(RuntimeError):
    """本次变更量超过告警阈值。"""

    def __init__(self, site, change_count, threshold, result):
        super().__init__(
            "站点 {} 变更量 {} 超过阈值 {}".format(site, change_count, threshold)
        )
        self.site = site
        self.change_count = change_count
        self.threshold = threshold
        self.result = result


class Store:
    """把每个站点最近一次快照按稳定键去重后存到本地 JSON 文件。"""

    def __init__(self, path=".listwatch_state.json"):
        self.path = path
        self._snapshots = self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}

    def _save(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".listwatch-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._snapshots, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        except BaseException:
            os.unlink(tmp_path)
            raise

    def get_snapshot(self, site):
        return [
            dict(record["item"])
            for record in self._snapshots.get(site, [])
            if isinstance(record, dict) and "item" in record
        ]

    def _change_count(self, result):
        # 顺序变化只是位移，不计入变更量告警
        return (
            len(result["added"])
            + len(result["removed"])
            + len(result.get("updated", []))
            + len(result.get("delisted", []))
        )

    def run(self, site, pages, alert_threshold=None):
        """对某个站点做一次增量比对，并把最新快照落盘。"""
        if pages and isinstance(pages[0], (list, tuple)):
            current = merge_pages(pages)
        else:
            current = merge_pages([pages])
        previous = self.get_snapshot(site)
        result = diff(previous, current)

        self._snapshots[site] = [
            {"key": stable_key(item), "item": item} for item in current
        ]
        self._save()

        result["site"] = site
        if not previous:
            # 首次运行只建基线，不把全量快照判成新增
            result = {
                "added": [],
                "removed": [],
                "updated": [],
                "reordered": [],
                "delisted": [],
                "site": site,
                "baseline": True,
                "change_count": 0,
            }
        else:
            result["baseline"] = False
            result["change_count"] = self._change_count(result)
        if (
            alert_threshold is not None
            and not result["baseline"]
            and result["change_count"] > alert_threshold
        ):
            raise ChangeThresholdExceeded(
                site, result["change_count"], alert_threshold, result
            )
        return result
