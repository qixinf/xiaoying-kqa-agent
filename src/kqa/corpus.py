"""合成文档库 + BM25 检索（段落级）。离线、确定性，便于测试复现。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

jieba.setLogLevel(60)  # 关掉 jieba 的初始化日志


def tokenize(text: str) -> list[str]:
    return [t for t in jieba.lcut(text.lower()) if t.strip() and not re.fullmatch(r"[\W_]+", t)]


@dataclass
class Doc:
    doc_id: str
    title: str
    version_date: str
    scope: str
    paragraphs: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs)


class Corpus:
    def __init__(self, path: str | Path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.docs: dict[str, Doc] = {d["doc_id"]: Doc(**d) for d in raw}
        # 段落级索引单元：(doc_id, 段落序号, 段落文本)
        self.units = [(d.doc_id, i, p) for d in self.docs.values() for i, p in enumerate(d.paragraphs)]
        self.bm25 = BM25Okapi([tokenize(self.docs[did].title + " " + p) for did, _, p in self.units])

    def search(self, query: str, top_k: int = 4) -> list[dict]:
        q = tokenize(query)
        if not q:
            return []
        scores = self.bm25.get_scores(q)
        ranked = sorted(zip(scores, self.units), key=lambda x: -x[0])
        hits = []
        for score, (did, idx, para) in ranked[:top_k]:
            if score <= 0:  # 没有任何词重叠，视为无结果，交给模型决定是否改写
                continue
            d = self.docs[did]
            hits.append({"doc_id": did, "para": idx, "title": d.title, "version_date": d.version_date,
                         "snippet": para, "score": round(float(score), 3)})
        return hits

    def read(self, doc_id: str) -> dict | None:
        d = self.docs.get(doc_id)
        if d is None:
            return None
        return {"doc_id": d.doc_id, "title": d.title, "version_date": d.version_date, "scope": d.scope,
                "paragraphs": [{"para": i, "text": p} for i, p in enumerate(d.paragraphs)]}
