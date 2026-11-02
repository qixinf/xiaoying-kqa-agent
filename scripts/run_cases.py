"""用真实模型批量跑 examples/cases.json，保存 Trace 与结果汇总。

用法：
  python scripts/run_cases.py --label baseline          # 跑全部用例
  python scripts/run_cases.py --only C04 C11 --label prompt-v2
  python scripts/run_cases.py --repeat 3 --label stability   # 每个用例跑 3 次，看稳定性

输出：
  examples/runs/<时间>-<label>/   每次运行单独存档：Cxx.json、results.md、meta.json
  examples/traces/Cxx.json         最近一次运行的 Trace（03-evaluation.md 引用这里）
  examples/results.md              最近一次运行的汇总表
  examples/runs/index.md           所有运行的通过率记录，便于对比每次修改的效果
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
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
    notes = []
    if r.status != case["expect_status"]:
        notes.append(f"状态 {r.status} ≠ 预期 {case['expect_status']}")
    if not set(case["expect_docs"]) <= cited:
        notes.append(f"引用 {sorted(cited)} 未覆盖预期 {case['expect_docs']}")
    if case["expect_status"] in {"no_info", "tool_failed", "incomplete"} and cited:
        notes.append(f"不应有引用，实际引用 {sorted(cited)}")
    return not notes, "；".join(notes) or "符合预期"


def git_sha() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "src", "data"], cwd=ROOT, text=True).strip()
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


def run_one(case: dict, llm) -> dict:
    corpus = Corpus(ROOT / case.get("data", "data/docs.json"))
    t0 = time.time()
    try:
        r = run_agent(case["question"], llm, corpus, ToolBox(corpus, case.get("fail"), case.get("max_retries", 1)),
                      case.get("history"), case.get("max_steps", 8))
    except Exception as e:  # 单个用例出错不影响其他用例
        return {"case": case, "passed": False, "note": f"运行异常 {type(e).__name__}: {e}", "status": "error",
                "steps_used": 0, "trace": [], "citations": [], "seconds": round(time.time() - t0, 1)}
    passed, note = check(case, r)
    return {"case": case, "passed": passed, "note": note, "seconds": round(time.time() - t0, 1), **r.to_dict()}


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--only", nargs="*", help="只跑指定用例，如 C02 C09")
    p.add_argument("--repeat", type=int, default=1, help="每个用例重复次数，用于观察真实模型的稳定性")
    p.add_argument("--label", default="run", help="本次运行的说明，如 baseline / prompt-v2")
    a = p.parse_args()

    cases = [c for c in json.loads((ROOT / "examples/cases.json").read_text(encoding="utf-8"))
             if not a.only or c["id"] in a.only]
    llm = OpenAIChat()
    started = datetime.now()
    run_dir = ROOT / "examples/runs" / f"{started:%Y%m%d-%H%M%S}-{a.label}"
    run_dir.mkdir(parents=True, exist_ok=True)

    rows, n_pass, n_total = [], 0, 0
    for case in cases:
        results = []
        for k in range(a.repeat):
            rec = run_one(case, llm)
            rec["mode"] = f"real_llm:{llm.model}"
            suffix = "" if a.repeat == 1 else f".r{k + 1}"
            (run_dir / f"{case['id']}{suffix}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append(rec)
        passes = sum(r["passed"] for r in results)
        n_pass, n_total = n_pass + passes, n_total + len(results)
        last = results[-1]
        actions = " → ".join(t["action"] for t in last["trace"])
        verdict = ("PASS" if passes == len(results) else "FAIL") + (f" {passes}/{len(results)}" if a.repeat > 1 else "")
        notes = "；".join(sorted({r["note"] for r in results}))
        rows.append(f"| {case['id']} | {'、'.join(case['accept'])} | {case['expect_status']} | {last['status']} | "
                    f"{last['steps_used']} | {actions} | {last['seconds']}s | {verdict} | {notes} |")
        print(rows[-1], flush=True)

    meta = {"label": a.label, "started": f"{started:%Y-%m-%d %H:%M:%S}", "model": llm.model,
            "base_url": os.getenv("LLM_BASE_URL") or "openai-default", "params": llm.extra,
            "dropped_params": llm.dropped_params, "git": git_sha(), "repeat": a.repeat,
            "passed": n_pass, "total": n_total}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    header = (f"# 用例运行结果\n\n"
              f"- 运行：`{run_dir.name}`　模型：`{llm.model}`　参数：`{json.dumps(llm.extra)}`"
              f"{'　被服务端拒绝的参数：' + str(llm.dropped_params) if llm.dropped_params else ''}\n"
              f"- 代码版本：`{meta['git']}`　时间：{meta['started']}　通过：**{n_pass}/{n_total}**\n\n"
              "| 用例 | 验收点 | 预期状态 | 实际状态 | 步数 | 动作序列 | 耗时 | 结果 | 说明 |\n"
              "|---|---|---|---|---|---|---|---|---|\n")
    table = header + "\n".join(rows) + "\n"
    (run_dir / "results.md").write_text(table, encoding="utf-8")

    # 全量、单次运行时，才覆盖"最新结果"，避免部分重跑把其他用例的 Trace 冲掉
    if not a.only and a.repeat == 1:
        latest = ROOT / "examples/traces"
        latest.mkdir(exist_ok=True)
        for f in run_dir.glob("C*.json"):
            shutil.copy(f, latest / f.name)
        (ROOT / "examples/results.md").write_text(table, encoding="utf-8")

    index = ROOT / "examples/runs/index.md"
    if not index.exists():
        index.write_text("# 运行记录\n\n| 运行 | 模型 | 参数 | 代码版本 | 范围 | 通过 |\n|---|---|---|---|---|---|\n", encoding="utf-8")
    scope = "全部" if not a.only else " ".join(a.only)
    with index.open("a", encoding="utf-8") as f:
        f.write(f"| [{run_dir.name}]({run_dir.name}/results.md) | {llm.model} | `{json.dumps(llm.extra)}` | "
                f"{meta['git']} | {scope}{' ×' + str(a.repeat) if a.repeat > 1 else ''} | {n_pass}/{n_total} |\n")

    print(f"\n通过 {n_pass}/{n_total}，结果保存在 {run_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
