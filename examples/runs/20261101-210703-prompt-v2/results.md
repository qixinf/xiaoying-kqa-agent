# 用例运行结果

- 运行：`20261101-210703-prompt-v2`　模型：`gpt-5.4-mini`　参数：`{}`　被服务端拒绝的参数：["reasoning_effort: Error code: 400 - Function tools with reasoning_effort are not supported for gpt-5.4-mini in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'."]
- 代码版本：`731be02`　时间：2026-11-01 21:07:03　通过：**6/6**

| 用例 | 验收点 | 预期状态 | 实际状态 | 步数 | 动作序列 | 耗时 | 结果 | 说明 |
|---|---|---|---|---|---|---|---|---|
| C05 | 上下文追问 | answered | answered | 3 | search_docs → read_doc → finish | 2.8s | PASS 3/3 | 符合预期 |
| C11 | 追问 | need_clarification | need_clarification | 2 | search_docs → finish | 2.4s | PASS 3/3 | 符合预期 |
