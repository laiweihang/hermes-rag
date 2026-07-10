# 检索质量评测脚手架

赫尔墨斯混合检索可以调 mode / α / top_k / rerank，但「调完到底变好
了还是变差了」需要一把尺子来量。`scripts/eval_retrieval.py` 就是这把
尺子：用一个手工标注的 dataset 跑指定参数的检索链路，输出
`hit@1 / hit@5 / MRR` 三项指标。

---

## 1. 数据集格式

JSONL，每行一条 case：

```jsonl
{"id": "q1", "question": "加班费按多少倍计算", "expected_sources": ["hr.md"], "expected_keywords": ["1.5", "倍"]}
```

| 字段                | 必需 | 说明                                                 |
|---------------------|------|------------------------------------------------------|
| `id`                | ✓    | 唯一标识，结果表用                                    |
| `question`          | ✓    | 用户问题原文                                          |
| `expected_sources`  | ◯    | 期望命中的文件名集合；命中其一即算 hit                 |
| `expected_keywords` | ◯    | 期望出现在召回 chunk 内容里的关键词；命中其一即算 hit  |

`expected_sources` 与 `expected_keywords` **至少要有一个**，否则脚本
报错退出。

### 三种判断精度

| 字段                 | 精度  | 何时用                                       |
|----------------------|-------|---------------------------------------------|
| `expected_sources`   | 粗    | 文档级命中——只要 chunk 来自正确文件就算对  |
| `expected_keywords`  | 中    | 内容级命中——chunk 里要出现关键词           |
| 两者都给（OR）       | 最宽松| 任一命中即 hit                              |

后续真要更严，可以加 `expected_chunk_ids`（精确到 chunk_id），但需要
先有「chunk_id 长稳」的入库流程，本项目暂未保证。

### 怎么从零造一份 10-20 条 dataset

1. 把真实问答日志（`messages` 表里的用户消息）按主题取样。
2. 对每条问题人工查一遍知识库，标注期望文件名。
3. 写 2-3 个该问题答案里**应该出现的关键词**（最好是数字、人名、
   术语等不易改写的 token）。
4. 跑一遍当前线上参数 baseline，把 `hit@5 < 0.5` 的 case 单独过一遍，
   确认是「数据集标注问题」还是「检索算法真的弱」。

---

## 2. 跑法

**前提**：在装好 `requirements.txt` 全部依赖的 venv 里跑（脚本会
import `retrieval` / `database` / `reranker`，触发 ChromaDB / jieba /
LangChain 重依赖）。

```bash
# 默认：weighted+α=0.5 + rerank
python3 scripts/eval_retrieval.py

# 切换 dataset
python3 scripts/eval_retrieval.py --dataset evals/my_dataset.jsonl

# 不同 mode 关 rerank 对比
python3 scripts/eval_retrieval.py --mode bm25 --no-rerank
python3 scripts/eval_retrieval.py --mode semantic --no-rerank
python3 scripts/eval_retrieval.py --mode weighted --alpha 0.7 --no-rerank
python3 scripts/eval_retrieval.py --mode rrf --rrf-k 40 --no-rerank

# 看 rerank 是否真有提升
python3 scripts/eval_retrieval.py --mode weighted --no-rerank
python3 scripts/eval_retrieval.py --mode weighted --rerank
```

输出：

```
▶ dataset=evals/dataset.example.jsonl  cases=5
▶ mode=weighted alpha=0.5 top_k=5 rerank=True rerank_top_n=5

id       hit@1  hit@5  first_rank question
--------------------------------------------------------------------------------
q1       ✓      ✓      1          加班费按多少倍计算
q2       ·      ✓      2          出差补贴每天多少钱
q3       ✓      ✓      1          年假有多少天
q4       ·      ✓      3          调休应该在多少天内安排
q5       ·      ·      -          病假需要哪些材料
--------------------------------------------------------------------------------
summary: n=5  hit@1=40.00%  hit@5=80.00%  MRR=0.5333
⏱  耗时 8.45s
💾 完整结果: evals/results/20260513_120000_weighted_rerank.json
```

每跑一次同时落 `evals/results/<ts>_<mode>_<rerank|norerank>.json`，
里面是每条 case 的 top_k 命中详情，方便事后 diff。

---

## 3. 怎么对比两次跑的结果

最朴素的方法：用 `jq` / Python 各跑一次抽 summary。

```bash
jq '{args: .args, summary}' \
  evals/results/20260513_*_weighted_norerank.json \
  evals/results/20260513_*_weighted_rerank.json
```

更进一步：写一个 `compare_results.py` 把两个 json 的 `results[*].id` 对齐，
看哪些 case 因为换 mode / 开 rerank 从「未命中」变「命中」（或反过来）。
本仓库暂未提供，需要时手工 diff 即可。

---

## 4. 已知局限

- **关键词命中漏报**：用户原文用「1.5 倍」，文档里写「150%」，
  `expected_keywords=["1.5"]` 不会命中。要么扩关键词列表，要么改
  用 source 级判断。
- **dataset 偏差**：手工 10-20 条样本极易过拟合到调参者的偏好。
  请把 dataset 当**回归基线**用，不要当「答案质量金标准」用。
- **Rerank 慢**：开了 rerank 每 case 要多调一次 LLM，5 条 case
  ≈ 5-15s。批量评测时建议先 `--no-rerank` 看融合层稳定性，再单独
  开 rerank 看精排带来的增量。
- **chunk_id 不稳**：ChromaDB 重建一次，所有 chunk_id 都换。所以
  本工具默认按 `expected_sources / expected_keywords` 判 hit，没有
  默认走 chunk_id 精确匹配。
- **裸 Python 跑不动**：依赖 ChromaDB / jieba / LangChain 全家桶。
  必须先 `pip install -r requirements.txt`。

---

## 5. 与 `/api/admin/retrieval/preview` 端点的区别

- **本脚本**：批量、客观指标、CLI、写文件，适合离线调参 / 回归。
- **preview 端点**：单次、人肉看 ranking 与 debug，适合在线一边
  调一边肉眼判断。两者搭配用。

---

## 6. 高阶增强功能实验

如果想观察 **简单重写 / HyDE / 上下文感知分块 / LLM 精排** 这四个开关
切换前后的检索效果差异，参见 [EXPERIMENTS.md](EXPERIMENTS.md)。该文档
包含教学语料 `demo/teaching/`、配套数据集 `evals/dataset.demo.jsonl`，
以及每个功能 "在哪个 UI 字段看到生效证据" 的对照说明。
