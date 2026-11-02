"""finish 的代码级校验：防止无依据回答和错误引用（本题的关键缺陷）。

校验规则（任一不满足即打回给模型修正）：
  结构   status 合法；answer 非空；citations 是 [{doc_id, quote}] 列表
  引用   文档存在；本轮已用 read_doc 读过；quote 是原文逐字片段（忽略空白差异）
  状态   answered ≥1 条引用；conflict ≥2 份不同文档；no_info / tool_failed 不带引用；
         need_clarification 必须给出追问
  过程   no_info 前必须至少成功检索过一次；tool_failed 前必须真的观察到过工具服务失败
         （防止不检索就说"没有资料"，或没有失败却谎报失败）
  数字   answered / conflict 的 answer 中出现的数字，必须出现在引用原文、被引文档的版本日期或用户问题中
         （启发式：拦截"引用 D01 却回答 15 天"这类张冠李戴，不做语义判断）
"""
from __future__ import annotations

import re

from .corpus import Corpus

VALID_STATUS = {"answered", "conflict", "no_info", "need_clarification", "tool_failed"}
_NUM = re.compile(r"\d+(?:\.\d+)?")
_DOC_REF = re.compile(r"(?<![A-Za-z0-9])D\d+(?!\d)", re.IGNORECASE)  # 中文紧邻时 \b 不生效


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _numbers(text: str) -> set[str]:
    return set(_NUM.findall(_DOC_REF.sub(" ", text or "")))


def validate_finish(args: dict, corpus: Corpus, read_doc_ids: set[str], question: str = "",
                    evidence: dict | None = None) -> list[str]:
    """evidence 由 agent 循环提供：{"searches_ok": 成功检索次数, "unavailable": 观察到的服务失败次数}。"""
    errors: list[str] = []
    status = args.get("status")
    answer = args.get("answer")
    cits = args.get("citations")

    # ---- 结构 ----
    if status not in VALID_STATUS:
        errors.append(f"status 非法：{status}，可选 {sorted(VALID_STATUS)}")
    if not isinstance(answer, str) or not answer.strip():
        errors.append("answer 不能为空")
        answer = ""
    if cits is None:
        cits = []
    if not isinstance(cits, list) or not all(isinstance(c, dict) for c in cits):
        return errors + ["citations 必须是 [{doc_id, quote}] 形式的列表"]

    # ---- 引用 ----
    valid_quotes: list[str] = []
    cited_docs: set[str] = set()
    for i, c in enumerate(cits):
        did, quote = str(c.get("doc_id", "")).strip(), str(c.get("quote", ""))
        doc = corpus.docs.get(did)
        if doc is None:
            errors.append(f"citations[{i}] 引用了不存在的文档 {did}")
            continue
        cited_docs.add(did)
        if did not in read_doc_ids:
            errors.append(f"citations[{i}] 引用的 {did} 本轮未用 read_doc 读取过原文")
        if not _norm(quote) or _norm(quote) not in _norm(doc.text):
            errors.append(f"citations[{i}] 的 quote 不是 {did} 原文中的逐字片段")
        else:
            valid_quotes.append(quote)

    # ---- 状态与引用是否匹配 ----
    if status == "answered" and not cits:
        errors.append("status=answered 必须至少有 1 条引用")
    if status == "conflict" and len(cited_docs) < 2:
        errors.append("status=conflict 必须引用至少 2 份不同文档")
    if status in {"no_info", "tool_failed"} and cits:
        errors.append(f"status={status} 不应附带引用")
    if status == "need_clarification" and not str(args.get("clarifying_question") or "").strip():
        errors.append("status=need_clarification 必须给出 clarifying_question")

    # ---- 过程证据 ----
    if evidence is not None:
        if status == "no_info" and evidence.get("searches_ok", 0) == 0:
            errors.append("status=no_info 之前必须至少成功执行一次 search_docs，才能判断资料中没有答案")
        if status == "tool_failed" and evidence.get("unavailable", 0) == 0:
            errors.append("status=tool_failed 但本轮没有观察到任何工具服务失败")

    # ---- 数字溯源（只在引用本身合法时检查，避免重复报错）----
    if status in {"answered", "conflict"} and valid_quotes and len(valid_quotes) == len(cits):
        allowed = _numbers(" ".join(valid_quotes)) | _numbers(question)
        for did in cited_docs:
            allowed |= _numbers(corpus.docs[did].version_date)
        unsupported = sorted(_numbers(answer) - allowed, key=float)
        if unsupported:
            errors.append(f"answer 中的数字 {unsupported} 在引用原文中找不到依据；请补充引用或删去该数字")
    return errors
