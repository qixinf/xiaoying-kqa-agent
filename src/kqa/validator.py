"""finish 的代码级校验：防止无依据回答和错误引用（本题的关键缺陷）。"""
from __future__ import annotations

import re

from .corpus import Corpus

VALID_STATUS = {"answered", "conflict", "no_info", "need_clarification", "tool_failed"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def validate_finish(args: dict, corpus: Corpus, read_doc_ids: set[str]) -> list[str]:
    errors: list[str] = []
    status = args.get("status")
    cits = args.get("citations") or []
    if status not in VALID_STATUS:
        errors.append(f"status 非法：{status}")
    for i, c in enumerate(cits):
        did, quote = c.get("doc_id", ""), c.get("quote", "")
        doc = corpus.docs.get(did)
        if doc is None:
            errors.append(f"citations[{i}] 引用了不存在的文档 {did}")
            continue
        if did not in read_doc_ids:
            errors.append(f"citations[{i}] 引用的 {did} 本轮未用 read_doc 读取过原文")
        if not quote or _norm(quote) not in _norm(doc.text):
            errors.append(f"citations[{i}] 的 quote 不是 {did} 原文中的逐字片段")
    cited_docs = {c.get("doc_id") for c in cits}
    if status == "answered" and not cits:
        errors.append("status=answered 必须至少有 1 条引用")
    if status == "conflict" and len(cited_docs) < 2:
        errors.append("status=conflict 必须引用至少 2 份不同文档")
    if status in {"no_info", "tool_failed"} and cits:
        errors.append(f"status={status} 不应附带引用")
    if status == "need_clarification" and not args.get("clarifying_question"):
        errors.append("status=need_clarification 必须给出 clarifying_question")
    return errors
