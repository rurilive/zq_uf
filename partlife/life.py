"""分区生命周期：按访问热度分层，归档后分批删除，失败可回滚、可恢复。"""

HOT = "hot"
COLD = "cold"
ARCHIVE = "archive"

DEFAULT_POLICY = {
    "hot_days": 7,    # 最近 N 天内有访问 -> 热层保留
    "cold_days": 30,  # 访问变少（hot..cold 天）-> 转冷层
    # 超过 cold_days 未访问 -> 先归档再删除
    "batch_size": 2,  # 删除分批大小
}


class DeleteError(Exception):
    """删除失败，触发当前批次回滚。"""


class Store:
    """模拟三层存储（hot/cold/archive）与读租约，归档和删除分别记录。"""

    def __init__(self):
        self.hot = {}        # (dataset, pid) -> 行数
        self.cold = {}       # (dataset, pid) -> 行数
        self.archive = {}    # location -> {"dataset", "id", "rows"}
        self.leases = {}     # (dataset, pid) -> 活跃读数
        self.archive_log = []  # 归档记录
        self.delete_log = []   # 删除批次记录，可单独重试
        self.fail_deletes = set()  # 模拟删除失败的分区 id

    def seed(self, dataset, partitions):
        for p in partitions:
            self.hot[(dataset, p["id"])] = p.get("rows", 0)
            if p.get("active_reads"):
                self.leases[(dataset, p["id"])] = p["active_reads"]

    def active_reads(self, dataset, pid):
        return self.leases.get((dataset, pid), 0)

    def move_cold(self, dataset, pid):
        self.cold[(dataset, pid)] = self.hot.pop((dataset, pid))
        return "cold://%s/%s" % (dataset, pid)

    def archive_part(self, dataset, pid):
        """归档是复制不是搬走：删除后仍有恢复入口。"""
        rows = self.hot.get((dataset, pid), 0)
        loc = "archive://%s/%s" % (dataset, pid)
        rec = {"dataset": dataset, "id": pid, "rows": rows}
        self.archive[loc] = rec
        self.archive_log.append(dict(rec, location=loc))
        return loc

    def delete_part(self, dataset, pid):
        if pid in self.fail_deletes:
            raise DeleteError(pid)
        self.hot.pop((dataset, pid), None)

    def restore(self, dataset, pid, rows):
        self.hot[(dataset, pid)] = rows


def plan(dataset, policy=None):
    """按访问热度分层；有活跃读取的分区跳过。tiers 只列非空层。"""
    pol = dict(DEFAULT_POLICY, **(policy or {}))
    name = dataset.get("name", "")
    parts = dataset.get("partitions")
    if parts is None:  # 兼容旧的按天输入
        parts = [{"id": d, "last_access_days": pol["cold_days"] + 1}
                 for d in dataset.get("days", [])]
    tiers, skipped = {}, []
    for p in parts:
        pid = p["id"]
        if p.get("active_reads", 0) > 0:
            skipped.append(pid)
            continue
        age = p.get("last_access_days", 0)
        tier = HOT if age <= pol["hot_days"] else COLD if age <= pol["cold_days"] else ARCHIVE
        tiers.setdefault(tier, []).append(pid)
    return {
        "dataset": name,
        "expired": [p["id"] for p in parts],
        "tiers": tiers,
        "skipped": skipped,
        "policy": pol,
    }


def _delete_batch(store, dataset, pids, batch_no):
    """删一个批次；任一失败则回滚本批次（从归档恢复）。"""
    done = []
    try:
        for pid in pids:
            store.delete_part(dataset, pid)
            done.append(pid)
    except DeleteError:
        for pid in done:
            rec = store.archive.get("archive://%s/%s" % (dataset, pid))
            if rec:
                store.restore(dataset, pid, rec["rows"])
        return {"batch": batch_no, "partitions": list(pids),
                "status": "rolled_back", "deleted": []}
    return {"batch": batch_no, "partitions": list(pids),
            "status": "done", "deleted": done}


def apply(plan_data, store=None, batch_size=None):
    """执行分层：冷层迁移、归档后分批删除；执行时仍按租约跳过。"""
    store = store or Store()
    name = plan_data.get("dataset", "")
    pol = plan_data.get("policy", DEFAULT_POLICY)
    bs = batch_size or pol.get("batch_size", DEFAULT_POLICY["batch_size"])
    tiers = plan_data.get("tiers") or {}
    skipped = list(plan_data.get("skipped", []))

    def leased(pid):
        return store.active_reads(name, pid) > 0

    cold_locations = {}
    for pid in tiers.get(COLD, []):
        if leased(pid):
            skipped.append(pid)
        elif (name, pid) in store.hot:
            cold_locations[pid] = store.move_cold(name, pid)

    to_delete = list(tiers.get(ARCHIVE, []))
    if not tiers:  # 兼容旧 plan：expired 直接删
        to_delete = list(plan_data.get("expired", []))
    to_delete, blocked = [p for p in to_delete if not leased(p)], [p for p in to_delete if leased(p)]
    skipped.extend(blocked)

    archived = [{"id": pid, "location": store.archive_part(name, pid)} for pid in to_delete]
    for a in archived:
        a["rows"] = store.archive[a["location"]]["rows"]

    batches, deleted = [], []
    for i in range(0, len(to_delete), bs):
        rec = _delete_batch(store, name, to_delete[i:i + bs], len(store.delete_log) + 1)
        store.delete_log.append(dict(rec, dataset=name))
        batches.append(rec)
        deleted.extend(rec["deleted"])

    return {
        "dataset": name,
        "tiered": {HOT: tiers.get(HOT, []), COLD: sorted(cold_locations)},
        "cold_locations": cold_locations,
        "archived": archived,
        "deleted": deleted,
        "skipped": skipped,
        "batches": len(batches),
        "batch_records": batches,
    }


def retry_batch(store, dataset, batch_no):
    """单独重试一个删除批次。"""
    for rec in store.delete_log:
        if rec["dataset"] == dataset and rec["batch"] == batch_no:
            if rec["status"] != "done":
                rec.update(_delete_batch(store, dataset, rec["partitions"], batch_no))
            return rec
    raise KeyError("no batch %s for dataset %s" % (batch_no, dataset))


def query_cold(store, dataset, start=None, end=None):
    """冷层按分区范围查询。"""
    return sorted(pid for (ds, pid) in store.cold
                  if ds == dataset
                  and (start is None or pid >= start)
                  and (end is None or pid <= end))


def query_archive(store, dataset, start=None, end=None):
    """归档范围查询，返回归档位置。"""
    return sorted(rec["location"] for rec in store.archive_log
                  if rec["dataset"] == dataset
                  and (start is None or rec["id"] >= start)
                  and (end is None or rec["id"] <= end))


def recover(store, dataset, pid, expected_rows=None):
    """从归档恢复到热层，恢复后校验行数。"""
    loc = "archive://%s/%s" % (dataset, pid)
    rec = store.archive.get(loc)
    if rec is None:
        return {"id": pid, "restored": False, "reason": "not_archived"}
    store.restore(dataset, pid, rec["rows"])
    rows = store.hot[(dataset, pid)]
    ok = rows == rec["rows"] and (expected_rows is None or rows == expected_rows)
    return {"id": pid, "location": loc, "rows": rows,
            "restored": ok, "rows_verified": ok}
