"""基于分片指纹的近重复检测。

流程：正文归一化 -> 字符分片（shingle）哈希指纹 -> 倒排索引按阈值召回
候选 -> 词集合相似度复核（长短文本分别归一）-> 输出相似度与判定依据。
"""

import hashlib
import pickle
import re
from collections import defaultdict

_WS = re.compile(r"\s+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

# 召回阈值 = 判定阈值 * _RECALL_FACTOR，召回放宽、复核从严。
_RECALL_FACTOR = 0.5
# 短文本词数 * _LENGTH_RATIO <= 长文本词数时视为长短差异大，改用包含度归一。
_LENGTH_RATIO = 3


def _normalize(text):
    return _WS.sub(" ", text.lower()).strip()


def _shingles(text, size):
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[i:i + size] for i in range(len(text) - size + 1)}


def _fingerprint(fragment):
    digest = hashlib.blake2b(fragment.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _word_set(text):
    """词集合：空白分词；含 CJK 的词元拆为汉字二元组，其余保留原样。"""
    words = set()
    for token in text.split():
        if _CJK_RUN.search(token):
            for run in _CJK_RUN.findall(token):
                if len(run) == 1:
                    words.add(run)
                else:
                    words.update(run[i:i + 2] for i in range(len(run) - 1))
        else:
            words.add(token)
    return words


def _blended(inter, size_a, size_b):
    """长短分别归一：长度悬殊用包含度，长度相近用 Jaccard。"""
    if not inter:
        return 0.0, "jaccard"
    shorter, longer = sorted((size_a, size_b))
    if shorter * _LENGTH_RATIO <= longer:
        return inter / shorter, "containment"
    return inter / (size_a + size_b - inter), "jaccard"


class DupFinder:
    """近重复检测器，支持增量索引与持久化。"""

    def __init__(self, threshold=0.6, shingle_size=5):
        self.threshold = threshold
        self.shingle_size = shingle_size
        self._docs = {}
        self._words = {}
        self._shingle_count = {}
        self._postings = defaultdict(set)

    def index(self, docs):
        """增量添加文档，返回索引中的文档总数。"""
        for doc in docs:
            doc_id = doc["id"]
            if doc_id in self._docs:
                self._remove(doc_id)
            text = _normalize(doc.get("text", ""))
            self._docs[doc_id] = text
            self._words[doc_id] = _word_set(text)
            hashes = {_fingerprint(frag) for frag in _shingles(text, self.shingle_size)}
            self._shingle_count[doc_id] = len(hashes)
            for fp in hashes:
                self._postings[fp].add(doc_id)
        return len(self._docs)

    def query(self, doc):
        """返回判定为近重复的文档 id 列表，按相似度降序。"""
        return [hit["id"] for hit in self.explain(doc)]

    def explain(self, doc):
        """返回命中详情：相似度、共同片段和判定依据。"""
        text = _normalize(doc.get("text", ""))
        frag_by_fp = {}
        for frag in _shingles(text, self.shingle_size):
            frag_by_fp.setdefault(_fingerprint(frag), frag)
        query_words = _word_set(text)
        self_id = doc.get("id")
        hits = []
        for doc_id, matched in self._candidates(frag_by_fp).items():
            if doc_id == self_id:
                continue
            shingle_score, _ = _blended(
                len(matched), len(frag_by_fp), self._shingle_count[doc_id])
            if shingle_score < self.threshold * _RECALL_FACTOR:
                continue
            doc_words = self._words[doc_id]
            score, metric = _blended(
                len(query_words & doc_words), len(query_words), len(doc_words))
            if score < self.threshold:
                continue
            common = sorted({frag_by_fp[fp] for fp in matched})[:5]
            if metric == "containment":
                basis = "词集合包含度 %.2f >= 阈值 %.2f（长短文本按较短者归一）"
            else:
                basis = "词集合 Jaccard 相似度 %.2f >= 阈值 %.2f（长度相近）"
            hits.append({
                "id": doc_id,
                "similarity": round(score, 4),
                "metric": metric,
                "common": common,
                "reason": (basis % (score, self.threshold))
                          + "；共同指纹片段 %d 个" % len(matched),
            })
        hits.sort(key=lambda hit: (-hit["similarity"], str(hit["id"])))
        return hits

    def save(self, path):
        with open(path, "wb") as fh:
            pickle.dump({
                "threshold": self.threshold,
                "shingle_size": self.shingle_size,
                "docs": self._docs,
                "words": self._words,
                "shingle_count": self._shingle_count,
                "postings": dict(self._postings),
            }, fh)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as fh:
            state = pickle.load(fh)
        finder = cls(threshold=state["threshold"],
                     shingle_size=state["shingle_size"])
        finder._docs = state["docs"]
        finder._words = state["words"]
        finder._shingle_count = state["shingle_count"]
        finder._postings = defaultdict(set, state["postings"])
        return finder

    def _candidates(self, frag_by_fp):
        """倒排索引召回：统计每个候选命中的指纹，跳过过热片段。"""
        df_cap = max(50, len(self._docs) // 100)
        hits = defaultdict(list)
        for fp in frag_by_fp:
            posting = self._postings.get(fp)
            if not posting or len(posting) > df_cap:
                continue
            for doc_id in posting:
                hits[doc_id].append(fp)
        return hits

    def _remove(self, doc_id):
        text = self._docs.pop(doc_id)
        self._words.pop(doc_id)
        self._shingle_count.pop(doc_id)
        for frag in _shingles(text, self.shingle_size):
            fp = _fingerprint(frag)
            posting = self._postings.get(fp)
            if posting:
                posting.discard(doc_id)
                if not posting:
                    del self._postings[fp]
