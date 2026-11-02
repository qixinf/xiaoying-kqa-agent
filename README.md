# 企业知识问答 Agent（小影校招 · 第 3 题）

## 环境与安装
Python 3.11+
```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 LLM_API_KEY；OpenAI 兼容接口均可
```

## 评审入口
```bash
pytest -q                                              # 离线测试（无需 Key）
cd src && python -m kqa.cli "报销期限多久？"            # 真实模型单题
cd src && python -m kqa.cli "报销期限多久？" --mock conflict   # 离线 Mock，仅验证流程
cd src && python -m kqa.cli "忘记密码怎么办？" --fail search_docs:1   # 注入工具失败
cd src && python -m kqa.cli --chat                     # 多轮追问
python scripts/run_cases.py                            # 跑全部用例 → examples/traces/ + examples/results.md
```
替换文档测试：`--data data/docs_no_conflict.json`（去掉 D02 后，"报销期限"应从 conflict 变为 answered）。

## 目录
- `src/kqa/` agent 循环、工具、校验器、模型接入
- `data/` 合成制度与 FAQ
- `examples/` 用例、Trace、结果汇总
- `docs/` 需求、方案、评估、AI 使用、复盘

## 已知限制
TODO：完成后填写（Mock 不证明真实决策效果；BM25 对口语化同义词依赖模型改写……）
