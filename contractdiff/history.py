"""历史契约版本管理：可回看、任意两版本 diff、连续版本批量扫描。"""

from .schema import normalize_contract
from .engine import DEFAULT_RULEBOOK


class History:
    """按版本号保存契约快照。版本号用字符串或数字均可，按登记顺序排列。"""

    def __init__(self):
        self._versions = []  # [(version, normalized_contract)]

    def add(self, version, contract):
        if any(v == version for v, _ in self._versions):
            raise ValueError(f"版本已存在: {version}")
        self._versions.append((version, normalize_contract(contract)))
        return self

    def versions(self):
        return [v for v, _ in self._versions]

    def get(self, version):
        """返回规范化契约的深拷贝，外部修改不影响历史快照。"""
        import copy
        for v, contract in self._versions:
            if v == version:
                return copy.deepcopy(contract)
        raise KeyError(f"未知版本: {version}")

    def diff(self, old_version, new_version, rulebook=None):
        """任意两个历史版本之间的兼容性检查。"""
        book = rulebook or DEFAULT_RULEBOOK
        return book.check(self.get(old_version), self.get(new_version))

    def scan(self, rulebook=None, include_disabled=False):
        """从早到晚逐个相邻版本扫描，供新规则上线前批量回放。

        返回 {"results": [...], "all_compatible": bool}。
        """
        book = rulebook or DEFAULT_RULEBOOK
        results = []
        for (v_old, c_old), (v_new, c_new) in zip(self._versions,
                                                  self._versions[1:]):
            result = book.check(c_old, c_new, include_disabled=include_disabled)
            results.append({
                "from_version": v_old,
                "to_version": v_new,
                "result": result,
            })
        all_compatible = all(r["result"]["compatible"] for r in results)
        return {"results": results, "all_compatible": all_compatible}
