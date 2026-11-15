"""采集出口计费：按采集结果计费，失败/超时/切换退费，按天落盘对账。

纯 Python 标准库实现。对外保持 ``charge(proxy, amount)`` 与
``settle(proxy)`` 两个入口，新增能力通过关键字参数和新函数暴露。

计费/退费口径见 ``REFUND_RULES`` 与 README：

* 抓取成功且页面入库（``success=True, stored=True``）：全额计费，不退费；
* 抓取超时（``timeout=True``）：全额退费；
* 抓取失败（``success=False``）：全额退费；
* 抓取成功但未入库（``stored=False``）：全额退费；
* 出口切换：旧出口在该批次内尚未结算的计费全额退费，切换记录关联原出口与批次。
"""

import json
import os
import tempfile
from datetime import datetime

# 退费规则：采集结果 -> 退费比例。1.0 表示全额退费，0.0 表示不退费（全额计费）。
REFUND_RULES = {
    "billed": 0.0,      # 成功且入库：全额计费
    "not_stored": 1.0,  # 成功但未入库：全额退费
    "failed": 1.0,     # 抓取失败：全额退费
    "timeout": 1.0,    # 抓取超时：全额退费
    "switch": 1.0,     # 出口切换，旧出口批次内未结算费用：全额退费
}


def _classify(success, stored, timeout):
    """根据采集结果判定计费口径。"""
    if timeout:
        return "timeout"
    if not success:
        return "failed"
    if not stored:
        return "not_stored"
    return "billed"


def _today():
    return datetime.now().date().isoformat()


def _now():
    return datetime.now().isoformat(timespec="seconds")


class SettleBook:
    """单本计费账：记录流水、切换记录、入库行数，并负责落盘。"""

    def __init__(self, data_dir=None):
        self.data_dir = data_dir
        self.entries = {}        # entry_id -> 计费流水
        self.switches = []       # 出口切换记录
        self.stored_rows = {}    # date -> proxy -> 入库行数
        self._entry_seq = 0
        self._switch_seq = 0
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            self._load()

    # ---------------------------------------------------------------- 落盘
    def _ledger_path(self):
        return os.path.join(self.data_dir, "ledger.json")

    def _settle_dir(self):
        path = os.path.join(self.data_dir, "settlements")
        os.makedirs(path, exist_ok=True)
        return path

    def _load(self):
        path = self._ledger_path()
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.entries = data.get("entries", {})
        self.switches = data.get("switches", [])
        self.stored_rows = data.get("stored_rows", {})
        self._entry_seq = data.get("entry_seq", 0)
        self._switch_seq = data.get("switch_seq", 0)

    def _flush(self):
        """流水全量写盘，保证重启后账不丢。"""
        if not self.data_dir:
            return
        self._atomic_write(self._ledger_path(), {
            "entries": self.entries,
            "switches": self.switches,
            "stored_rows": self.stored_rows,
            "entry_seq": self._entry_seq,
            "switch_seq": self._switch_seq,
        })

    def _atomic_write(self, path, data):
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---------------------------------------------------------------- 计费
    def charge(self, proxy, amount, *, success=True, stored=True,
               timeout=False, batch="default", reason=""):
        """记录一次采集出口调用并按采集结果计费/退费。

        默认 ``success=True, stored=True``，因此旧式 ``charge(proxy, amount)``
        调用仍表示一次成功入库的计费，行为与历史接口一致。
        """
        amount = round(float(amount), 2)
        outcome = _classify(success, stored, timeout)
        self._entry_seq += 1
        entry_id = "e%d" % self._entry_seq
        refund = round(amount * REFUND_RULES[outcome], 2)
        entry = {
            "id": entry_id,
            "proxy": proxy,
            "amount": amount,
            "batch": batch,
            "success": bool(success),
            "stored": bool(stored),
            "timeout": bool(timeout),
            "outcome": outcome,
            "refund": refund,
            "refund_reason": outcome if refund > 0 else None,
            "reason": reason,
            "ts": _now(),
            "settled": None,
        }
        self.entries[entry_id] = entry
        self._flush()
        return {
            "proxy": proxy,
            "amount": amount,
            "balance": self.balance(proxy),
            "refund": refund,
            "outcome": outcome,
            "entry_id": entry_id,
            "batch": batch,
        }

    def balance(self, proxy):
        """出口当前净额 = 累计计费 - 累计退费。"""
        return round(
            sum(e["amount"] - e["refund"]
                for e in self.entries.values()
                if e["proxy"] == proxy),
            2,
        )

    def _refund_entry(self, entry, reason):
        entry["refund"] = round(entry["amount"] * REFUND_RULES.get(reason, 1.0), 2)
        entry["refund_reason"] = reason

    # ---------------------------------------------------------------- 切换
    def switch_proxy(self, old_proxy, new_proxy, batch="default", reason="switch"):
        """切换采集出口。

        旧出口在该批次内、尚未结算且仍在计费的流水全额退费；切换记录关联
        原出口（``old_proxy``）、新出口（``new_proxy``）与批次（``batch``），
        并记下被退费的流水 id，便于对账追溯。
        """
        self._switch_seq += 1
        switch_id = "s%d" % self._switch_seq
        refund_entry_ids = []
        refund_amount = 0.0
        for entry in self.entries.values():
            if (entry["proxy"] == old_proxy
                    and entry["batch"] == batch
                    and entry["settled"] is None
                    and entry["refund"] == 0.0):
                self._refund_entry(entry, reason)
                refund_entry_ids.append(entry["id"])
                refund_amount += entry["refund"]
        record = {
            "id": switch_id,
            "batch": batch,
            "old_proxy": old_proxy,
            "new_proxy": new_proxy,
            "reason": reason,
            "refund_entry_ids": refund_entry_ids,
            "refund_amount": round(refund_amount, 2),
            "ts": _now(),
        }
        self.switches.append(record)
        self._flush()
        return record

    # ------------------------------------------------------------ 入库行数
    def mark_stored(self, proxy, rows, date=None):
        """登记某出口某天的页面入库行数，用于结算对账。"""
        date = date or _today()
        self.stored_rows.setdefault(date, {})[proxy] = int(rows)
        self._flush()
        return {"date": date, "proxy": proxy, "stored_rows": int(rows)}

    # ---------------------------------------------------------------- 结算
    def settle(self, proxy, date=None, stored_rows=None):
        """按天结算出口费用，并与入库行数对账。

        返回计费总额、退费总额、余额、计费条数、入库行数与差异
        （``diff = 计费条数 - 入库行数``），结果按天写入
        ``settlements/YYYY-MM-DD.json``。
        """
        date = date or _today()
        day_entries = [
            e for e in self.entries.values()
            if e["proxy"] == proxy and e["ts"].startswith(date)
        ]
        charges = round(sum(e["amount"] for e in day_entries), 2)
        refunds = round(sum(e["refund"] for e in day_entries), 2)
        billable = [
            e for e in day_entries
            if e["outcome"] == "billed" and e["refund"] == 0.0
        ]

        if stored_rows is None:
            stored_rows = self.stored_rows.get(date, {}).get(proxy)
        if stored_rows is None:
            stored_rows_out, diff, reconciled = None, 0.0, False
        else:
            stored_rows_out = int(stored_rows)
            diff = float(len(billable) - stored_rows_out)
            reconciled = True

        result = {
            "proxy": proxy,
            "date": date,
            "charges": charges,
            "charge_count": len(day_entries),
            "refunds": refunds,
            "refund_count": sum(1 for e in day_entries if e["refund"] > 0),
            "balance": round(charges - refunds, 2),
            "billable_count": len(billable),
            "stored_rows": stored_rows_out,
            "diff": diff,
            "reconciled": reconciled,
            "entries": day_entries,
            "switches": [
                s for s in self.switches
                if (s["old_proxy"] == proxy or s["new_proxy"] == proxy)
                and s["ts"].startswith(date)
            ],
        }
        for entry in day_entries:
            entry["settled"] = date
        self._flush()
        self._persist_settlement(date, proxy, result)
        return result

    def _persist_settlement(self, date, proxy, result):
        if not self.data_dir:
            return
        path = os.path.join(self._settle_dir(), date + ".json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        else:
            doc = {"date": date, "proxies": {}}
        doc["proxies"][proxy] = result
        self._atomic_write(path, doc)

    def load_settlement(self, date=None):
        """读取某天已落盘的结算单，未落盘返回 ``None``。"""
        if not self.data_dir:
            return None
        date = date or _today()
        path = os.path.join(self.data_dir, "settlements", date + ".json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


# ----------------------------------------------------------------- 默认账
# 未配置数据目录时只在内存记账（兼容历史用法与单测）；调用 init(data_dir)
# 或设置环境变量 PROXYSETTLE_DATA_DIR 后即启用按天落盘、重启可恢复。
_book = SettleBook(os.environ.get("PROXYSETTLE_DATA_DIR"))


def init(data_dir):
    """打开（或创建）指定数据目录的账本并设为默认账本。"""
    global _book
    _book = SettleBook(data_dir)
    return _book


def reset(data_dir=None):
    """清空当前进程的默认账本，主要用于测试隔离。"""
    global _book
    _book = SettleBook(data_dir)
    return _book


def charge(proxy, amount, **kwargs):
    """记录一次采集调用，见 ``SettleBook.charge``。"""
    return _book.charge(proxy, amount, **kwargs)


def settle(proxy, **kwargs):
    """按天结算并对账落盘，见 ``SettleBook.settle``。"""
    return _book.settle(proxy, **kwargs)


def switch_proxy(old_proxy, new_proxy, batch="default", reason="switch"):
    """切换采集出口并退旧出口批次费用，见 ``SettleBook.switch_proxy``。"""
    return _book.switch_proxy(old_proxy, new_proxy, batch, reason)


def mark_stored(proxy, rows, date=None):
    """登记入库行数，见 ``SettleBook.mark_stored``。"""
    return _book.mark_stored(proxy, rows, date)


def balance(proxy):
    """查询出口净额，见 ``SettleBook.balance``。"""
    return _book.balance(proxy)


def load_settlement(date=None):
    """读取已落盘的日结算单，见 ``SettleBook.load_settlement``。"""
    return _book.load_settlement(date)
