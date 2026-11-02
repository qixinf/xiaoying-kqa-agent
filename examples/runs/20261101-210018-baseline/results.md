# 用例运行结果

- 运行：`20261101-210018-baseline`　模型：`gpt-5.4-mini`　参数：`{}`　被服务端拒绝的参数：['reasoning_effort']
- 代码版本：`a2f38c5`　时间：2026-11-01 21:00:18　通过：**10/11**

| 用例 | 验收点 | 预期状态 | 实际状态 | 步数 | 动作序列 | 耗时 | 结果 | 说明 |
|---|---|---|---|---|---|---|---|---|
| C01 | 明确答案 | answered | answered | 3 | search_docs → read_doc → finish | 4.0s | PASS | 符合预期 |
| C02 | 文档冲突 | conflict | conflict | 3 | search_docs → read_doc → read_doc → finish | 3.8s | PASS | 符合预期 |
| C03 | 资料外问题 | no_info | no_info | 4 | search_docs → search_docs → search_docs → finish | 4.7s | PASS | 符合预期 |
| C04 | 改写后的相似问题 | answered | answered | 3 | search_docs → read_doc → finish | 3.0s | PASS | 符合预期 |
| C05 | 上下文追问 | answered | answered | 3 | search_docs → read_doc → finish | 3.1s | PASS | 符合预期 |
| C06 | 工具失败、正常恢复 | answered | answered | 4 | search_docs → search_docs → read_doc → finish | 3.8s | PASS | 符合预期 |
| C07 | 工具失败 | tool_failed | tool_failed | 3 | search_docs → search_docs → finish | 2.6s | PASS | 符合预期 |
| C08 | 文档冲突（可查证） | answered | answered | 3 | search_docs → read_doc → read_doc → finish | 3.6s | PASS | 符合预期 |
| C09 | 替换文档后动作改变 | answered | answered | 3 | search_docs → read_doc → finish | 2.7s | PASS | 符合预期 |
| C10 | 步数上限 | incomplete | incomplete | 2 | search_docs → read_doc → read_doc | 1.9s | PASS | 符合预期 |
| C11 | 追问 | need_clarification | answered | 3 | search_docs → read_doc → finish | 3.7s | FAIL | 状态 answered ≠ 预期 need_clarification |
