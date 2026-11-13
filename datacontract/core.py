"""只记字段列表的契约登记。"""


class Registry:
    def __init__(self):
        self.contracts = {}

    def register(self, contract):
        self.contracts[contract["name"]] = list(contract.get("fields", []))
        return self.contracts[contract["name"]]

    def diff(self, old, new):
        removed = [name for name in old if name not in new]
        added = [name for name in new if name not in old]
        return {"added": added, "removed": removed}
