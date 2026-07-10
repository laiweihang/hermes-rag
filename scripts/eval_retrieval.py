#!/usr/bin/env python3
"""检索质量评测脚手架（CLI）。

用法示例：
    # 默认 weighted+rerank 跑示例 dataset
    python3 scripts/eval_retrieval.py

    # 不同 mode 对比
    python3 scripts/eval_retrieval.py --mode bm25 --no-rerank
    python3 scripts/eval_retrieval.py --mode semantic --no-rerank
    python3 scripts/eval_retrieval.py --mode weighted --alpha 0.7 --no-rerank
    python3 scripts/eval_retrieval.py --mode rrf --rerank --top-k 5

dataset 格式（JSONL，每行一条）：
    {"id": "q1",
     "question": "加班费按多少倍计算",
     "expected_sources": ["hr.md"],         # 文件名集合，命中其一即 hit
     "expected_keywords": ["1.5", "倍"]}    # 命中 chunk 内容即 hit（可选）

指标：
    - hit@1 / hit@5：top_k 中是否命中 expected_sources 或 expected_keywords。
    - MRR：第一个命中 chunk 的 1/rank（无命中=0），跨 case 求均值。

每次跑结束会把完整结果落到 ``evals/results/<ts>_<mode>.json``，便于后续 diff。
注意：本脚本会 import ``retrieval`` / ``database`` / ``rag_engine``，需要在
装好 requirements 的 venv 里执行；裸 Python 会卡在依赖导入。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# 让脚本能从仓库根 import 业务模块。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval_retrieval")


@dataclass
class Case:
    id: str
    question: str
    expected_sources: list[str] = field(default_factory=list)
    expected_keywords: list[str] = field(default_factory=list)


@dataclass
class CaseResult:
    id: str
    question: str
    hits: list[dict]            # [{rank, source, content_preview, hit_by}]
    hit_at_1: bool
    hit_at_5: bool
    first_hit_rank: Optional[int]   # 1-based；None = 未命中


def _load_dataset(path: str) -> list[Case]:
    cases: list[Case] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"❌ {path}:{lineno} JSON 解析失败: {e}")
            if "question" not in row or "id" not in row:
                raise SystemExit(f"❌ {path}:{lineno} 缺少必需字段 id/question")
            if "expected_sources" not in row and "expected_keywords" not in row:
                raise SystemExit(
                    f"❌ {path}:{lineno} 必须至少提供 expected_sources 或 expected_keywords 之一"
                )
            cases.append(Case(
                id=row["id"],
                question=row["question"],
                expected_sources=row.get("expected_sources", []),
                expected_keywords=row.get("expected_keywords", []),
            ))
    return cases


def _is_hit(doc_source: str, doc_content: str, case: Case) -> Optional[str]:
    """返回命中原因字符串（"source" / "keyword:<kw>"）；未命中返回 None。"""
    src_base = os.path.basename(doc_source or "")
    if case.expected_sources and src_base in set(case.expected_sources):
        return "source"
    for kw in case.expected_keywords or []:
        if kw and kw in (doc_content or ""):
            return f"keyword:{kw}"
    return None


def _eval_one(case: Case, hybrid_kwargs: dict, do_rerank: bool, rerank_top_n: int):
    from retrieval import hybrid_search

    triples = hybrid_search(case.question, **hybrid_kwargs)
    if do_rerank and len(triples) > rerank_top_n:
        from reranker import rerank as _rerank
        triples = _rerank(case.question, triples, top_n=rerank_top_n)

    hits_log = []
    first_hit_rank: Optional[int] = None
    for rank, (doc, _score, _debug) in enumerate(triples, 1):
        meta = doc.metadata or {}
        source = meta.get("source", "?")
        content = doc.page_content or ""
        why = _is_hit(source, content, case)
        if why and first_hit_rank is None:
            first_hit_rank = rank
        hits_log.append({
            "rank": rank,
            "source": os.path.basename(source),
            "content_preview": content[:120],
            "hit_by": why,
        })

    hit_at_1 = bool(hits_log and hits_log[0]["hit_by"])
    hit_at_5 = any(h["hit_by"] for h in hits_log[:5])

    return CaseResult(
        id=case.id,
        question=case.question,
        hits=hits_log,
        hit_at_1=hit_at_1,
        hit_at_5=hit_at_5,
        first_hit_rank=first_hit_rank,
    )


def _summarize(results: list[CaseResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"n": 0}
    hit1 = sum(1 for r in results if r.hit_at_1) / n
    hit5 = sum(1 for r in results if r.hit_at_5) / n
    mrr = sum((1.0 / r.first_hit_rank) if r.first_hit_rank else 0.0 for r in results) / n
    return {
        "n": n,
        "hit@1": round(hit1, 4),
        "hit@5": round(hit5, 4),
        "MRR": round(mrr, 4),
    }


def _print_table(results: list[CaseResult], summary: dict[str, Any]) -> None:
    print()
    print(f"{'id':<8} {'hit@1':<6} {'hit@5':<6} {'first_rank':<10} question")
    print("-" * 80)
    for r in results:
        first = str(r.first_hit_rank) if r.first_hit_rank else "-"
        print(
            f"{r.id:<8} {'✓' if r.hit_at_1 else '·':<6} "
            f"{'✓' if r.hit_at_5 else '·':<6} {first:<10} {r.question[:50]}"
        )
    print("-" * 80)
    print(
        f"summary: n={summary['n']}  "
        f"hit@1={summary['hit@1']:.2%}  "
        f"hit@5={summary['hit@5']:.2%}  "
        f"MRR={summary['MRR']:.4f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="检索质量评测脚手架")
    parser.add_argument("--dataset", default="evals/dataset.example.jsonl")
    parser.add_argument("--mode", default="weighted",
                        choices=["weighted", "rrf", "semantic", "bm25"])
    parser.add_argument("--alpha", type=float, default=0.5,
                        help="weighted 模式 sem 权重")
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--bm25-top-k", type=int, default=20)
    parser.add_argument("--vector-top-k", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=5,
                        help="融合后保留数（也作 hit@K 中的 K）")
    parser.add_argument("--semantic-threshold", type=float, default=0.0)
    parser.add_argument("--rerank", dest="rerank", action="store_true", default=True)
    parser.add_argument("--no-rerank", dest="rerank", action="store_false")
    parser.add_argument("--rerank-top-n", type=int, default=None,
                        help="精排后给评测用的 top_n；默认等于 --top-k")
    parser.add_argument("--out-dir", default="evals/results")
    args = parser.parse_args()

    cases = _load_dataset(args.dataset)
    if not cases:
        print(f"⚠️ dataset 为空：{args.dataset}")
        return 1

    hybrid_kwargs = {
        "mode": args.mode,
        "alpha": args.alpha,
        "rrf_k": args.rrf_k,
        "bm25_top_k": args.bm25_top_k,
        "vector_top_k": args.vector_top_k,
        "final_top_k": args.top_k,
        "semantic_threshold": args.semantic_threshold,
        "enable_bm25": True,
    }
    rerank_top_n = args.rerank_top_n or args.top_k

    print(f"▶ dataset={args.dataset}  cases={len(cases)}")
    print(f"▶ mode={args.mode} alpha={args.alpha} top_k={args.top_k} "
          f"rerank={args.rerank} rerank_top_n={rerank_top_n}")

    t0 = time.time()
    results = [_eval_one(c, hybrid_kwargs, args.rerank, rerank_top_n) for c in cases]
    elapsed = time.time() - t0

    summary = _summarize(results)
    _print_table(results, summary)
    print(f"⏱  耗时 {elapsed:.2f}s")

    os.makedirs(args.out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    rerank_tag = "rerank" if args.rerank else "norerank"
    out_path = os.path.join(args.out_dir, f"{ts}_{args.mode}_{rerank_tag}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "args": vars(args),
            "summary": summary,
            "elapsed_seconds": elapsed,
            "results": [asdict(r) for r in results],
        }, f, ensure_ascii=False, indent=2)
    print(f"💾 完整结果: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
