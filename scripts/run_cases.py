"""批量跑 examples/cases.json，保存每个用例的 Trace，并生成结果汇总表。

用法：python scripts/run_cases.py            # 真实模型（需 .env）
      python scripts/run_cases.py --only C02 C06
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from kqa.agent import run_agent  # noqa: E402
from kqa.corpus import Corpus  # noqa: E402
from kqa.llm import OpenAIChat  # noqa: E402
from kqa.tools import ToolBox  # noqa: E402


def check(case: dict, r) -> tuple[bool, str]:
    cited = {c["doc_id"] for c in r.citations}
    ok_status = r.status == case["expect_status"]
    ok_docs = set(case["expect_docs"]) <= cited
    note = [] if ok_status else [f"状态 {r.status} ≠ 预期 {case['expect_status']}"]
    if not ok_docs:
        note.append(f"引用 {sorted(cited)} 未覆盖预期 {case['expect_docs']}")
    return ok_status and ok_docs, "；".join(note) or "符合预期"


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--only", nargs="*")
    a = p.parse_args()

    cases = json.loads((ROOT / "examples/cases.json").read_text(encoding="utf-8"))
    out_dir = ROOT / "examples/traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    llm = OpenAIChat()
    rows = []
    for case in cases:
        if a.only and case["id"] not in a.only:
            continue
        corpus = Corpus(ROOT / case.get("data", "data/docs.json"))
        r = run_agent(case["question"], llm, corpus, ToolBox(corpus, case.get("fail")),
                      case.get("history"), case.get("max_steps", 8))
        passed, note = check(case, r)
        record = {"case": case, "mode": f"real_llm:{llm.model}", "passed": passed, "note": note, **r.to_dict()}
        (out_dir / f"{case['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        actions = " → ".join(t["action"] for t in r.trace)
        rows.append(f"| {case['id']} | {'、'.join(case['accept'])} | {case['expect_status']} | {r.status} | "
                    f"{r.steps_used} | {actions} | {'PASS' if passed else 'FAIL'} | {note} |")
        print(rows[-1])

    header = (f"# 用例运行结果\n\n模型：{llm.model}　运行时间：{datetime.now():%Y-%m-%d %H:%M}\n\n"
              "| 用例 | 验收点 | 预期状态 | 实际状态 | 步数 | 动作序列 | 结果 | 说明 |\n|---|---|---|---|---|---|---|---|\n")
    (ROOT / "examples/results.md").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
