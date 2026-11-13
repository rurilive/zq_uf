"""只登记字段名的元数据。"""


class Registry:
    def __init__(self):
        self.datasets = {}

    def register(self, dataset):
        self.datasets[dataset["name"]] = list(dataset.get("fields", []))
        return self.datasets[dataset["name"]]

    def diff(self, source):
        registered = self.datasets.get(source.get("name"), [])
        fields = list(source.get("fields", []))
        return {
            "added": [name for name in fields if name not in registered],
            "removed": [name for name in registered if name not in fields],
        }
