# 混合检索（BM25 + 语义向量）

赫尔墨斯 RAG 检索从「纯 ChromaDB 语义相似度」升级为「BM25 关键词 +
语义向量」的可调混合策略，目的是支持**调参实验**——同一问题用不
同 mode/参数对比召回质量，验证检索算法如何设计。

---

## 1. 设计概览

```
              ┌──────────────────┐
   query ───► │  hybrid_search   │
              └──────────────────┘
                  │           │
        BM25 召回 ▼           ▼ 语义召回
   ┌──────────────────┐  ┌──────────────────┐
   │  BM25Index       │  │  Chroma vs       │
   │  (jieba 分词,    │  │  similarity_     │
   │   rank_bm25)     │  │  search          │
   └──────────────────┘  └──────────────────┘
                  │           │
                  ▼           ▼
              ┌──────────────────┐
              │   融合层          │
              │  weighted / rrf   │
              └──────────────────┘
                       │
                       ▼
              ┌──────────────────┐
              │   Rerank（可选）  │  LLM-as-reranker
              │   reranker.py    │  → 0-10 分排序
              └──────────────────┘
                       │
                       ▼
            top_n Documents + debug → LLM context
```

四种 `mode`：

| mode       | 行为                                                 |
|------------|------------------------------------------------------|
| `semantic` | 单走 Chroma 向量相似度（与改造前一致）              |
| `bm25`     | 单走 BM25 关键词                                    |
| `weighted` | 两路各取 top_k → min-max 归一化 → α·sem + (1-α)·bm25 |
| `rrf`      | 两路各取 top_k → Σ 1/(k+rank)                       |

`enable_bm25=False` 时无论 mode 是什么都强制退化为 `semantic`，作为
全局兜底开关。

---

## 2. 参数

### 2.1 全局默认（DB 持久化）

`models.RetrievalSettings` 单行表（id=1），首次启动由 `init_db` 用
`config.HYBRID_*` seed：

| 字段                 | 默认       | 含义                                    |
|----------------------|------------|-----------------------------------------|
| `mode`               | `weighted` | 融合策略                                 |
| `alpha`              | `0.5`      | weighted 模式 sem 权重；bm25=1-α        |
| `rrf_k`              | `60`       | RRF 公式中的常数                         |
| `bm25_top_k`         | `20`       | BM25 召回数                              |
| `vector_top_k`       | `20`       | 语义召回数                               |
| `final_top_k`        | `5`        | 融合后保留数                             |
| `semantic_threshold` | `0.5`      | 语义通道相关度阈值                       |
| `enable_bm25`        | `True`     | 全局 BM25 开关                          |

环境变量覆盖（仅作 seed 默认）：

```bash
HYBRID_DEFAULT_MODE=weighted
HYBRID_ALPHA=0.5
HYBRID_RRF_K=60
HYBRID_BM25_TOP_K=20
HYBRID_VECTOR_TOP_K=20
```

### 2.2 单次请求覆盖

`QueryRequest` / `MessageCreate` 多了一个可选字段
`retrieval: RetrievalParams`，所有子字段都可选；**仅传非 None 的
字段做覆盖**，其余沿用 DB 全局默认。

### 2.3 三层优先级

```
DB 全局默认  ←——  legacy `top_k` 字段  ←——  request.retrieval
（最低）                                       （最高）
```

`top_k` 仅向后兼容旧调用方，等价于覆盖 `final_top_k`。

---

## 3. 接口

### 3.1 问答路由（透传 retrieval）

`POST /query` / `POST /api/conversations/{id}/messages` /
`POST /api/conversations/{id}/messages/stream` 都接受可选
`retrieval` 字段：

```json
{
  "question": "加班费怎么算？",
  "retrieval": {
    "mode": "rrf",
    "rrf_k": 60,
    "final_top_k": 8
  }
}
```

不传 `retrieval` 时与改造前行为一致（默认 `weighted + α=0.5`，向量
仍占一半权重）。

### 3.2 admin 端点

需要管理员身份。

#### `GET /api/admin/retrieval` → `RetrievalSettingsOut`

读取当前生效的全局参数。

#### `PUT /api/admin/retrieval` → `RetrievalSettingsOut`

局部更新，仅写入非 None 字段，更新后清缓存。例：

```bash
curl -X PUT http://localhost:8000/api/admin/retrieval \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"rrf","rrf_k":40,"enable_bm25":true}'
```

#### `POST /api/admin/retrieval/preview` → `RetrievalPreviewResponse`

跑一次 `hybrid_search`，返回每条候选的 ranking 调试信息（**不调
LLM**）。这是「验证检索算法如何设计」的核心入口。

请求：

```json
{
  "query": "加班费按 1.5 倍",
  "params": { "mode": "weighted", "alpha": 0.7 }
}
```

响应：

```json
{
  "mode": "weighted",
  "used_params": { "mode": "weighted", "alpha": 0.7, ... },
  "items": [
    {
      "id": "hr.md#12345678",
      "source": "hr.md",
      "content": "加班费按 1.5 倍计算 ...（截断 200 字）",
      "bm25_rank": 1,
      "bm25_norm": 1.0,
      "sem_rank": 2,
      "sem_norm": 0.92,
      "fused_score": 0.964
    }
  ]
}
```

`bm25_rank=null` 或 `sem_rank=null` 表示该条仅命中另一路。

---

## 3.5 Rerank（LLM-as-reranker）

混合检索给出 `final_top_k` 条候选后，可以再调一次 LLM 让它对每条
候选打 0-10 分（[`reranker.py`](../reranker.py)），按分数取
`rerank_top_n` 真正喂给主 LLM。

### 何时开 / 何时关

- **开**：召回数较多（>10）、token 富裕、追求答案聚焦时。一次精排
  通常能把噪声 chunk 洗掉。
- **关**：追求最低首 token 时延（rerank 是阻塞调用，多 1-3s）；
  或主 LLM 已经很强（GPT-4 级）能自己消化噪声。

### 关键参数

| 参数（`RetrievalSettings` 列） | 默认 | 说明                                    |
|-------------------------------|------|-----------------------------------------|
| `rerank_enabled`              | True | 全局开关；关掉 = 行为完全等同未引入 rerank |
| `rerank_top_n`                | 5    | 精排后真正喂给 LLM 的最终条数             |
| `rerank_provider_id`          | NULL | 指定 reranker Provider；NULL 走默认       |

环境变量：`RERANK_DEFAULT_ENABLED` / `RERANK_DEFAULT_TOP_N` /
`RERANK_DEFAULT_PROVIDER_ID`（仅 seed 用）。

### 延迟与成本

- 候选 20 条 × 300 字 ≈ 6k tokens 输入；按 GPT-4o-mini 价位约
  $0.001/请求。
- 延迟典型 1-3s，受 LLM 响应速度主导。
- 强烈建议给 `rerank_provider_id` 指定一个**便宜的小模型**
  （如 4o-mini / Qwen-Turbo / DeepSeek-V3）做 reranker，主问答用大
  模型——「召回精排分离」是检索系统的标准做法。

### 失败兜底

- LLM 调用异常 → logger.warning + 退回原排序的前 `top_n`。
- LLM 输出无可解析评分（解析正则一行不中）→ 同上。
- 候选数 ≤ `top_n` → 跳过 LLM 调用，直接返回。

### 与 `final_top_k` 的关系

`final_top_k` 现在的语义是「精排输入数」（也是 rerank 关闭时给
LLM 的数量）；`rerank_top_n` 才是开 rerank 后真正给 LLM 的数量。
建议 `final_top_k=20, rerank_top_n=5`。

---

## 4. BM25 索引生命周期

进程内单例 `retrieval.BM25Index`，**lazy 重建**：检索时若 `_stale`
则从 ChromaDB 全量拉切片重新分词建库。

`mark_stale()` 钩子在以下成功路径末尾被调用，确保索引始终一致：

| 端点 / 入口                                   | 文件:行号               |
|-----------------------------------------------|-------------------------|
| `POST /upload`                                | `api.py:472`            |
| `DELETE /api/documents/{name}`                | `api.py:553`            |
| `POST /api/documents/{name}/reingest` (删旧)  | `api.py:573`            |
| `POST /api/documents/{name}/reingest` (加新)  | `api.py:595`            |
| `PUT /api/chunks/{id}`                        | `api.py:741`            |
| `DELETE /api/chunks/{id}`                     | `api.py:758`            |
| `POST /api/admin/vectorstore/rebuild`         | `api.py:2149`           |
| `ingest.ingest_files()` 批量入库              | `ingest.py:119`         |

---

## 5. 文件改动清单

| 文件                | 类型 | 说明                                                 |
|---------------------|------|------------------------------------------------------|
| `retrieval.py`      | NEW  | 分词 / BM25 单例 / 两种融合 / `hybrid_search`       |
| `requirements.txt`  | M    | 追加 `rank_bm25`、`jieba`                            |
| `config.py`         | M    | `HYBRID_*` seed 默认值                               |
| `models.py`         | M    | `RetrievalSettings` ORM + `_ensure_retrieval_settings` |
| `schemas.py`        | M    | `RetrievalParams` / `*Out` / `*Update` / `Preview*`  |
| `rag_engine.py`     | M    | 替换两处 similarity_search 为 `hybrid_search`        |
| `api.py`            | M    | 3 个 admin 端点 + 7 处 `mark_stale` + retrieval 透传 |
| `ingest.py`         | M    | 批量入库后 `mark_stale`                              |

---

## 6. 调参建议

### 6.1 何时偏向 BM25（降 α / 改 mode=bm25）

- 查询是**专有名词、人名、型号、编号、日期**——语义向量对这类
  长尾词召回不稳定，BM25 命中精确。
- 文档里包含大量代码 / 配置 / 表格——语义模型对这种结构化文本
  的相似度区分度差。

### 6.2 何时偏向语义（升 α / 改 mode=semantic）

- 查询是**问句、改述、概念性问法**（"为啥…"、"怎么…"），
  关键词不一定出现在原文。
- 多语言互检（中文问 → 英文文档），BM25 完全失效。

### 6.3 RRF vs Weighted

- **RRF**：对两路分数尺度不敏感，适合「不知道两路谁更可信」时
  的安全默认；调参面少，只有 `rrf_k` 一个旋钮（越大越平滑）。
- **Weighted**：可解释性强，能精确控制两路占比；但依赖 min-max
  归一化，对极端分布敏感。

---

## 7. 验证

### 7.1 静态

```bash
python3 -m py_compile retrieval.py rag_engine.py api.py \
  schemas.py models.py config.py database.py ingest.py
```

### 7.2 融合数学单测

参见 `/tmp/test_fusion.py`（仓库外）。验证 `_min_max_normalize`、
`_fuse_weighted`（α=0/0.5/1.0）、`_fuse_rrf`、`tokenize` 的代数性
质。**已通过**：

- α=0 → 退化为纯 BM25 排序
- α=1 → 退化为纯语义排序
- α=0.5 → 双路重叠 id 占前列
- RRF → 双路 rank 之和决定排名

### 7.3 端到端

需要装好 `requirements.txt` 全部依赖 + 配好 embedding Provider，然后：

```bash
# 同一问题分别用四种 mode preview
for m in semantic bm25 weighted rrf; do
  curl -s -X POST http://localhost:8000/api/admin/retrieval/preview \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"加班费\",\"params\":{\"mode\":\"$m\"}}" \
    | jq ".items | map({id, bm25_rank, sem_rank, fused_score})"
done
```

观察四种 mode 的命中集合与排序差异——这就是「验证检索算法如何
设计」的工作流。

---

## 8. 已知局限

- **冷启动**：BM25 首次检索需重建索引（拉全量切片 + jieba 分词）。
  几千切片 < 1s，万级别可能数秒。
- **多 worker 部署**：当前 uvicorn 单 worker 假设；若开多 worker，
  各进程的 BM25 单例独立，`mark_stale` 不跨进程同步——本期不
  支持。
- **embedding 维度变更**：管理员切换 embedding Provider 后需手动
  触发 `/api/admin/vectorstore/rebuild`，BM25 会随重建一同失效。
- **chunk_id 来源**：BM25 通道直接拿 ChromaDB 内部 id；语义通道
  从 `metadata._id` 或 `(source, hash(content))` 复合键派生——
  两路在融合时按这个 id 对齐。若 chunk 元数据无 `_id` 且 source
  相同的两条切片碰巧 hash 冲突（10⁸ 取模），融合层会误并；实
  践概率极低，可忽略。
