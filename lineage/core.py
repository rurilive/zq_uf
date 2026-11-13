"""只记一跳血缘。"""


class Lineage:
    def __init__(self):
        self.records = {}

    def record(self, job):
        self.records[job["name"]] = {
            "inputs": list(job.get("inputs", [])),
            "outputs": list(job.get("outputs", [])),
        }
        return self.records[job["name"]]

    def impact(self, table):
        return [
            name for name, item in self.records.items() if table in item["inputs"]
        ]
