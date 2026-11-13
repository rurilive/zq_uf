"""按正文完全相等判重。"""


class DupFinder:
    def __init__(self):
        self.docs = {}

    def index(self, docs):
        for doc in docs:
            self.docs[doc["id"]] = doc.get("text", "")
        return len(self.docs)

    def query(self, doc):
        text = doc.get("text", "")
        return [key for key, value in self.docs.items() if value == text]
