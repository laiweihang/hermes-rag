# RAG 质量评测与回归门禁

## 覆盖范围

`evals/rag_quality.jsonl` 当前包含 38 条案例：人事制度、财务、会议纪要、
知识库无答案问题，以及带恶意指令的 Prompt Injection 文档。

评测指标：

- `answer_correctness`：回答命中人工标注事实的比例；开启 `--judge` 后由 LLM Judge 比较标准答案。
- `faithfulness`：回答中的事实是否能被本轮检索证据支持。
- `citation_precision`：答案中的 `[n]` 是否有效且对应证据支持该句。
- `citation_coverage`：应当引用的事实句中，实际附带引用的比例。
- `retrieval_hit_rate`：是否召回标注来源。
- `refusal_accuracy`：知识库没有答案时是否明确拒答，而不是编造。
- `prompt_injection_resistance`：是否忽略文档中的恶意指令和伪造事实。
- `latency_mean_ms` / `latency_p95_ms`：端到端延迟。
- `input_tokens` / `output_tokens`：优先采用 Provider 返回的真实 usage，缺失时才做中英文混合估算。
- `token_usage_estimated_cases`：本轮有多少案例因 Provider 未返回 usage 而使用估算值。
- `estimated_cost_usd`：根据命令行传入的百万 Token 单价估算调用成本。

## 运行方法

直接调用当前项目配置的模型和向量库：

```powershell
.\.venv\Scripts\python.exe scripts\eval_rag.py
```

评测一个已经运行的 API：

```powershell
$env:RAG_EVAL_USERNAME="admin"
$env:RAG_EVAL_PASSWORD="你的密码"
.\.venv\Scripts\python.exe scripts\eval_rag.py `
  --base-url http://127.0.0.1:8000 `
  --input-price-per-million 2 `
  --output-price-per-million 8
```

使用当前默认 LLM 作为 Judge：

```powershell
.\.venv\Scripts\python.exe scripts\eval_rag.py --judge
```

完整结果写入 `evals/results/`，并同步刷新 `latest.json`。任一指标违反
`evals/thresholds.json` 时程序返回退出码 `2`，CI 会直接失败。

## 调整阈值

阈值分为：

- `minimum`：准确率、忠实度、引用、拒答和安全指标不得低于该值。
- `maximum`：P95 延迟和总成本不得高于该值。

先用固定模型和固定语料跑出稳定基线，再逐步提高阈值。不要因为一次失败直接
降低阈值，应先查看 `latest.json` 中对应案例的答案、来源和单项得分。

## CI

默认 CI 运行：

1. Python 编译、单元测试和 FastAPI 集成测试。
2. 前端 ESLint、生产构建和 Playwright 端到端测试。
3. 配置仓库变量 `RAG_EVAL_BASE_URL` 后，每次提交额外调用测试环境执行真实 RAG 回归。

真实回归还需要 Secrets：`RAG_EVAL_USERNAME`、`RAG_EVAL_PASSWORD`。成本变量为
`RAG_INPUT_PRICE_PER_MILLION`、`RAG_OUTPUT_PRICE_PER_MILLION`。

## 设计依据

本项目采用与 RAGAS 相同的“正确性、忠实度、上下文和引用分离评估”思想，并参考
ARES 将检索相关性、回答忠实度、回答相关性拆开诊断。离线启发式用于无 API 的 CI，
LLM Judge 用于发布前评估；二者不应被理解为人工审阅的完全替代。

- RAGAS: https://aclanthology.org/2024.eacl-demo.16/
- ARES: https://aclanthology.org/2024.naacl-long.20/
