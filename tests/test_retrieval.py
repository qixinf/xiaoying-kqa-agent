"""数据与检索的契约测试：确认合成数据确实能支撑每个用例。

每条断言对应 examples/cases.json 中的一个用例。若改了 data/docs.json 或分词方式导致这里失败，
说明用例的前提被破坏了，需要先修数据，而不是去改模型 prompt。
"""
from pathlib import Path

import pytest

from kqa.corpus import Corpus

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Corpus(ROOT / "data/docs.json")
NO_CONFLICT = Corpus(ROOT / "data/docs_no_conflict.json")


def doc_ids(query: str, corpus: Corpus = CORPUS) -> list[str]:
    return [h["doc_id"] for h in corpus.search(query)]


@pytest.mark.parametrize("case, query, expected", [
    ("C01", "入职 3 年的员工年假有几天？", "D03"),
    ("C04", "我上班两年了，一年能歇几天带薪的假？", "D03"),   # 口语原句也能命中
    ("C05", "深圳 出差 住宿标准", "D04"),                    # 模型从历史补全后的查询
    ("C06", "忘记密码怎么办？", "D08"),
    ("C07", "年假要提前几天申请？", "D03"),
])
def test_answerable_questions_hit_expected_doc(case, query, expected):
    assert doc_ids(query)[0] == expected, case


def test_c02_conflict_both_docs_retrieved():
    assert {"D01", "D02"} <= set(doc_ids("报销期限多久？"))


def test_c08_old_and_new_policy_both_retrieved():
    # 旧版 D05 排在前面：模型必须读原文，发现 D06 写明"替代 D05"，不能直接用第一条
    ids = doc_ids("现在每周可以远程办公几天？")
    assert ids[0] == "D05" and "D06" in ids


def test_c09_no_conflict_corpus_only_has_d01():
    ids = doc_ids("报销期限多久？", NO_CONFLICT)
    assert "D01" in ids and "D02" not in ids


def test_c03_out_of_scope_only_returns_near_miss():
    # 资料外问题：只返回"员工福利"这类相近但不含答案的文档，任何片段都不含"期权"
    hits = CORPUS.search("公司的股票期权怎么行权？")
    assert hits and hits[0]["doc_id"] == "D09"
    assert all("期权" not in h["snippet"] for h in hits)


def test_vocabulary_gap_needs_rewrite():
    # BM25 的已知局限："休假"与"年假"无词面重叠，返回空，需要模型改写查询
    assert doc_ids("休假") == []
    assert doc_ids("年假")[0] == "D03"
