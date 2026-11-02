"""工具层契约测试：统一返回结构、错误类型、retryable 标记、参数容错、失败注入。"""
from pathlib import Path

import pytest

from kqa.corpus import Corpus
from kqa.tools import MAX_TOP_K, TOOL_SCHEMAS, ToolBox, normalize_doc_id

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Corpus(ROOT / "data/docs.json")


def tb(fail=None):
    return ToolBox(CORPUS, fail)


# ---------- search_docs ----------
def test_search_ok_structure():
    r = tb().call("search_docs", {"query": "年假 天数"})
    assert r["ok"] is True
    h = r["data"]["hits"][0]
    assert set(h) >= {"doc_id", "para", "title", "version_date", "scope", "snippet", "score"}
    assert h["doc_id"] == "D03" and h["scope"] == "全体正式员工"


def test_search_empty_result_has_hint():
    r = tb().call("search_docs", {"query": "休假"})
    assert r["ok"] is True and r["data"]["count"] == 0 and "hint" in r["data"]


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "   "}, {"query": "年假" * 60}, {"query": "年假", "top_k": "多"}])
def test_search_bad_args(args):
    r = tb().call("search_docs", args)
    assert r == {**r, "ok": False, "error_type": "bad_args", "retryable": False}


def test_search_top_k_clamped():
    assert tb().call("search_docs", {"query": "员工", "top_k": 99})["data"]["count"] <= MAX_TOP_K
    assert tb().call("search_docs", {"query": "员工", "top_k": 1})["data"]["count"] == 1


# ---------- read_doc ----------
@pytest.mark.parametrize("raw", ["D03", "d03", " D3 ", "d3"])
def test_read_doc_id_normalized(raw):
    r = tb().call("read_doc", {"doc_id": raw})
    assert r["ok"] and r["data"]["doc_id"] == "D03"


def test_read_doc_not_found_not_retryable():
    r = tb().call("read_doc", {"doc_id": "D99"})
    assert r["error_type"] == "not_found" and r["retryable"] is False


def test_normalize_keeps_unknown_format():
    assert normalize_doc_id("POLICY-1") == "POLICY-1"


# ---------- 失败注入 ----------
def test_fail_once_then_recover():
    box = tb({"search_docs": 1})
    first = box.call("search_docs", {"query": "密码"})
    assert first["ok"] is False and first["error_type"] == "unavailable" and first["retryable"] is True
    assert box.call("search_docs", {"query": "密码"})["ok"] is True


def test_fail_always():
    box = tb({"read_doc": -1})
    assert all(not box.call("read_doc", {"doc_id": "D01"})["ok"] for _ in range(3))


def test_bad_args_do_not_consume_failure_budget():
    # 参数错误在失败注入之前就被拦下，不应消耗"前 1 次失败"的额度
    box = tb({"search_docs": 1})
    assert box.call("search_docs", {"query": ""})["error_type"] == "bad_args"
    assert box.call("search_docs", {"query": "密码"})["error_type"] == "unavailable"


# ---------- 兜底 ----------
def test_unknown_tool():
    r = tb().call("publish", {})
    assert r["ok"] is False and r["error_type"] == "bad_args"


def test_internal_error_never_raises(monkeypatch):
    box = tb()
    monkeypatch.setattr(box.corpus, "search", lambda *a, **k: 1 / 0)
    r = box.call("search_docs", {"query": "年假"})
    assert r["ok"] is False and r["error_type"] == "internal"


def test_schema_names_match_tools():
    assert [t["function"]["name"] for t in TOOL_SCHEMAS] == ["search_docs", "read_doc", "finish"]
