"""混合检索模块（BM25 关键词 + 语义向量）。

本模块是 RAG 检索层的统一入口。对外暴露：

- ``tokenize(text)``      → jieba 分词 + 简单清洗。
- ``BM25Index``           → 进程内 BM25 单例索引，支持 lazy 重建 / mark stale。
- ``hybrid_search(...)``  → 四种 mode（semantic / bm25 / weighted / rrf）
                            的统一检索接口；返回 (Document, fused_score, debug)。

设计目标：
1. **可调参验证**：通过 ``mode`` / ``alpha`` / ``rrf_k`` / 各路 top_k 等参数
   在请求级或全局级灵活切换，便于对比不同检索策略的效果。
2. **零数据复制**：BM25 与 ChromaDB 共用同一份切片数据 —— BM25Index
   重建时直接从 ``database.get_collection`` 拉全量。
3. **失效便宜**：写入 / 删除 chunks 后只需 ``mark_stale()``，下次检索时
   才真正重建，避免每次 CRUD 都重新分词建表。

线程安全：``BM25Index`` 用 ``threading.Lock`` 保护重建过程，避免多线程
请求同时进入 rebuild 把同一份数据建两遍。FastAPI 默认线程池处理同步
路由，因此该锁是必要的。
"""

from __future__ import annotations

import logging
import math
import re
import threading
from typing import Iterable, Literal

import jieba
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

from database import get_collection, get_embeddings

logger = logging.getLogger(__name__)


# 关闭 jieba 启动 banner —— 否则首次调用时会在日志里刷一行
# "Building prefix dict ..."，对生产日志噪音较大。
jieba.setLogLevel(logging.WARNING)


_TOKEN_STRIP_RE = re.compile(r"^[\s。，！？、；：'\"《》（）()【】\[\]{}—\-_…·.,!?;:]+$")


def tokenize(text: str) -> list[str]:
    """jieba 分词 + 轻量清洗，返回 token 列表。

    步骤：
        1. None / 空串 → 返回 []。
        2. ``jieba.lcut`` 切词；自动支持中英文混排。
        3. 去掉纯空白 token；纯标点 token；英文转小写。

    没有引入停用词表 —— BM25 自身的 IDF 会让"的/了"等高频词权重很低，
    人工停用词表对中文知识库收益有限且增加维护成本。
    """
    if not text:
        return []
    raw = jieba.lcut(text)
    out: list[str] = []
    for tok in raw:
        if not tok or tok.isspace():
            continue
        # 纯标点直接丢弃
        if _TOKEN_STRIP_RE.match(tok):
            continue
        out.append(tok.lower())
    return out


# ==========================================
# BM25 索引（进程内单例）
# ==========================================


class BM25Index:
    """BM25 全量索引的进程内单例。

    生命周期：
        - 第一次调用 ``ensure_ready()`` 时 lazy 构建。
        - 任何写入 / 删除 chunks 的路径调用 ``mark_stale()``。
        - 下一次 ``ensure_ready()`` 检测到 stale 后重建。

    存储结构：
        ``_ids[i]`` 与 ``_docs[i]`` / ``_metas[i]`` / ``_tokens[i]`` 一一对应；
        ``_bm25`` 是 ``BM25Okapi`` 实例，基于 ``_tokens``。
        全部为内存字段，进程退出即释放。
    """

    _instance: "BM25Index | None" = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._stale = True
        self._ids: list[str] = []
        self._docs: list[str] = []
        self._metas: list[dict] = []
        self._tokens: list[list[str]] = []
        self._bm25: BM25Okapi | None = None

    # ---- 单例工厂 ----
    @classmethod
    def instance(cls) -> "BM25Index":
        if cls._instance is None:
            # 双重检查锁 —— 防止冷启动并发请求把单例构造两次。
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---- 失效与重建 ----
    def mark_stale(self) -> None:
        """标记索引过期。下一次检索时会触发 rebuild。"""
        # 不加锁也可：bool 赋值是原子操作。
        self._stale = True

    def ensure_ready(self) -> None:
        """如果索引已过期或未构建，则重建。"""
        if not self._stale and self._bm25 is not None:
            return
        with self._lock:
            # 双重检查：等锁过程中可能已有其它线程完成重建。
            if not self._stale and self._bm25 is not None:
                return
            self._rebuild_locked()

    def _rebuild_locked(self) -> None:
        """实际的重建过程；调用方需先持有 ``self._lock``。"""
        col = get_collection()
        total = col.count()
        if total == 0:
            self._ids = []
            self._docs = []
            self._metas = []
            self._tokens = []
            self._bm25 = None
            self._stale = False
            logger.info("🧱 BM25 重建：向量库为空，索引置空")
            return

        # 一次性拉全量；ChromaDB 不支持服务端分词/排序，只能内存处理。
        result = col.get(include=["documents", "metadatas"], limit=total)
        ids = result.get("ids") or []
        docs = result.get("documents") or []
        metas = result.get("metadatas") or []

        self._ids = list(ids)
        self._docs = [d or "" for d in docs]
        self._metas = [m or {} for m in metas]
        self._tokens = [tokenize(d) for d in self._docs]
        # rank_bm25 要求至少一条非空 token 列表，否则 IDF 计算报错。
        non_empty = [t if t else ["__empty__"] for t in self._tokens]
        self._bm25 = BM25Okapi(non_empty)
        self._stale = False
        logger.info(f"🧱 BM25 重建完成：{len(self._ids)} 个切片")

    # ---- 检索 ----
    def search(self, query: str, top_k: int) -> list[tuple[str, str, dict, float]]:
        """对 BM25 索引做 top_k 检索，返回 ``(id, doc, meta, score)`` 列表。

        无索引或 query 分词为空时返回 []。score 是 BM25Okapi 原始得分
        （非负浮点数，越大越相关），在融合层会做 min-max 归一化。
        """
        self.ensure_ready()
        if self._bm25 is None or not self._ids:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = self._bm25.get_scores(q_tokens)
        # argsort 按 score 降序取前 top_k；忽略所有得分 ≤ 0 的（无任何 token 命中）。
        idxs = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out: list[tuple[str, str, dict, float]] = []
        for i in idxs:
            if scores[i] <= 0:
                break
            out.append((self._ids[i], self._docs[i], self._metas[i], float(scores[i])))
            if len(out) >= top_k:
                break
        return out


# ==========================================
# 语义检索通道（封装 LangChain 调用）
# ==========================================


def _semantic_search(
    query: str, top_k: int, threshold: float
) -> list[tuple[str, str, dict, float]]:
    """语义通道。返回与 BM25 同形状的元组列表，分数为 LangChain 归一化相关度（0~1）。

    ChromaDB 默认距离是 L2/cosine，LangChain 的
    ``similarity_search_with_relevance_scores`` 会换算成 0~1 的相关度。
    threshold 仅在此通道做过滤；融合后不再二次过滤。
    """
    if top_k <= 0:
        return []
    col = get_collection()
    total = col.count()
    if total == 0:
        return []
    query_embedding = get_embeddings().embed_query(query)
    result = col.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, total),
        include=["documents", "metadatas", "distances"],
    )
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    out: list[tuple[str, str, dict, float]] = []
    for i, chunk_id in enumerate(ids):
        doc = docs[i] if i < len(docs) else ""
        meta = dict(metas[i] if i < len(metas) and metas[i] else {})
        distance = float(distances[i]) if i < len(distances) else float("inf")
        # 与 LangChain Chroma 默认欧氏距离换算保持一致。
        score = 1.0 - distance / math.sqrt(2)
        if score < threshold:
            continue
        meta["_id"] = str(chunk_id)
        meta.setdefault("chunk_id", str(chunk_id))
        out.append((str(chunk_id), doc, meta, score))
    return out


# ==========================================
# 融合算法
# ==========================================


def _min_max_normalize(values: Iterable[float]) -> dict[int, float]:
    """对 enumerate(values) 做 min-max 归一化，返回 {index: normalized}。

    只取列表的极值参与归一化；全相等时统一返回 1.0（避免除零）。
    """
    vals = list(values)
    if not vals:
        return {}
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return {i: 1.0 for i in range(len(vals))}
    return {i: (v - lo) / (hi - lo) for i, v in enumerate(vals)}


def _fuse_weighted(
    bm25_hits: list[tuple[str, str, dict, float]],
    sem_hits: list[tuple[str, str, dict, float]],
    alpha: float,
) -> list[tuple[str, str, dict, float, dict]]:
    """加权融合：先各自归一化，再按 α*sem + (1-α)*bm25 累加。

    返回元素：``(id, doc, meta, fused_score, debug_dict)``。
    debug_dict 含两路的 rank（1-based）与归一化分数，便于"验证算法设计"。
    """
    bm25_norms = _min_max_normalize(s for _, _, _, s in bm25_hits)
    sem_norms = _min_max_normalize(s for _, _, _, s in sem_hits)

    # 按 chunk_id 合并两路命中
    merged: dict[str, dict] = {}
    for rank, (cid, doc, meta, _raw) in enumerate(bm25_hits):
        merged[cid] = {
            "doc": doc,
            "meta": meta,
            "bm25_rank": rank + 1,
            "bm25_norm": bm25_norms.get(rank, 0.0),
            "sem_rank": None,
            "sem_norm": 0.0,
        }
    for rank, (cid, doc, meta, _raw) in enumerate(sem_hits):
        slot = merged.setdefault(
            cid,
            {
                "doc": doc,
                "meta": meta,
                "bm25_rank": None,
                "bm25_norm": 0.0,
                "sem_rank": None,
                "sem_norm": 0.0,
            },
        )
        slot["sem_rank"] = rank + 1
        slot["sem_norm"] = sem_norms.get(rank, 0.0)

    out: list[tuple[str, str, dict, float, dict]] = []
    for cid, slot in merged.items():
        fused = alpha * slot["sem_norm"] + (1.0 - alpha) * slot["bm25_norm"]
        debug = {
            "bm25_rank": slot["bm25_rank"],
            "bm25_norm": round(slot["bm25_norm"], 4),
            "sem_rank": slot["sem_rank"],
            "sem_norm": round(slot["sem_norm"], 4),
            "fused": round(fused, 4),
        }
        out.append((cid, slot["doc"], slot["meta"], fused, debug))
    out.sort(key=lambda x: x[3], reverse=True)
    return out


def _fuse_rrf(
    bm25_hits: list[tuple[str, str, dict, float]],
    sem_hits: list[tuple[str, str, dict, float]],
    rrf_k: int,
) -> list[tuple[str, str, dict, float, dict]]:
    """倒数排名融合（Reciprocal Rank Fusion）。

    每路按降序排名 r∈[1..N]，命中得分 ``1/(rrf_k + r)``，两路求和。
    不需要分数归一化，对量纲差异鲁棒；rrf_k 越大，对靠后名次越宽容。
    """
    merged: dict[str, dict] = {}
    for rank, (cid, doc, meta, _raw) in enumerate(bm25_hits):
        slot = merged.setdefault(
            cid,
            {
                "doc": doc,
                "meta": meta,
                "bm25_rank": None,
                "sem_rank": None,
                "score": 0.0,
            },
        )
        slot["bm25_rank"] = rank + 1
        slot["score"] += 1.0 / (rrf_k + (rank + 1))
    for rank, (cid, doc, meta, _raw) in enumerate(sem_hits):
        slot = merged.setdefault(
            cid,
            {
                "doc": doc,
                "meta": meta,
                "bm25_rank": None,
                "sem_rank": None,
                "score": 0.0,
            },
        )
        slot["sem_rank"] = rank + 1
        slot["score"] += 1.0 / (rrf_k + (rank + 1))

    out: list[tuple[str, str, dict, float, dict]] = []
    for cid, slot in merged.items():
        debug = {
            "bm25_rank": slot["bm25_rank"],
            "bm25_norm": None,
            "sem_rank": slot["sem_rank"],
            "sem_norm": None,
            "fused": round(slot["score"], 6),
        }
        out.append((cid, slot["doc"], slot["meta"], slot["score"], debug))
    out.sort(key=lambda x: x[3], reverse=True)
    return out


# ==========================================
# 对外统一接口
# ==========================================


HybridMode = Literal["weighted", "rrf", "semantic", "bm25"]


def hybrid_search(
    query: str,
    *,
    mode: HybridMode = "weighted",
    alpha: float = 0.5,
    rrf_k: int = 60,
    bm25_top_k: int = 20,
    vector_top_k: int = 20,
    final_top_k: int = 5,
    semantic_threshold: float = 0.0,
    enable_bm25: bool = True,
    bm25_query: str | None = None,
    semantic_query: str | None = None,
) -> list[tuple[Document, float, dict]]:
    """混合检索统一入口。

    Args:
        query: 用户查询文本。两个 *_query 参数为 None 时，作为 BM25 与
            语义两路的查询文本；非 None 则各自覆盖（用于查询重写场景：
            BM25 用关键词重写、向量用 HyDE 假答案）。
        mode: 融合模式。``semantic`` / ``bm25`` 走单通道；``weighted`` /
            ``rrf`` 走两路融合。``enable_bm25=False`` 时无论 mode 是什么
            都强制退化为 semantic。
        alpha: weighted 模式下 semantic 通道权重，范围 [0,1]。
        rrf_k: RRF 公式 ``1/(k+rank)`` 的常数。
        bm25_top_k / vector_top_k: 召回阶段两路各取多少候选。
        final_top_k: 融合后保留的最终条数。
        semantic_threshold: semantic 通道的相关度阈值，低于该值不入候选。
        bm25_query: 可选；覆盖 BM25 通道的查询文本（默认用 ``query``）。
        semantic_query: 可选；覆盖向量通道的查询文本（默认用 ``query``）。

    Returns:
        ``[(Document, fused_score, debug_dict), ...]``，按 fused_score
        降序、长度 ≤ final_top_k。debug_dict 含两路的 rank 与归一化分数，
        供 ``/api/admin/retrieval/preview`` 展示。
    """
    if not query or not query.strip():
        return []

    # 通道查询解析：未覆盖则用原 query。两路解耦后，外层可以让 BM25 走
    # 关键词重写、向量走 HyDE 假答案，互不干扰。
    bm25_q = bm25_query if (bm25_query and bm25_query.strip()) else query
    sem_q = semantic_query if (semantic_query and semantic_query.strip()) else query

    # 安全降级：BM25 被全局开关关掉时，weighted/rrf/bm25 都退化。
    if not enable_bm25:
        mode = "semantic"

    # 单通道模式：直接调对应函数，不进入融合层。
    if mode == "semantic":
        hits = _semantic_search(sem_q, vector_top_k, semantic_threshold)
        return [
            (
                Document(page_content=doc, metadata=meta),
                score,
                {
                    "bm25_rank": None,
                    "bm25_norm": None,
                    "sem_rank": rank + 1,
                    "sem_norm": None,
                    "fused": round(score, 4),
                },
            )
            for rank, (_id, doc, meta, score) in enumerate(hits[:final_top_k])
        ]

    if mode == "bm25":
        hits = BM25Index.instance().search(bm25_q, bm25_top_k)
        return [
            (
                Document(page_content=doc, metadata=meta),
                score,
                {
                    "bm25_rank": rank + 1,
                    "bm25_norm": None,
                    "sem_rank": None,
                    "sem_norm": None,
                    "fused": round(score, 4),
                },
            )
            for rank, (_id, doc, meta, score) in enumerate(hits[:final_top_k])
        ]

    # 双通道融合：先各自召回，再融合。
    bm25_hits = BM25Index.instance().search(bm25_q, bm25_top_k)
    sem_hits = _semantic_search(sem_q, vector_top_k, semantic_threshold)

    if mode == "weighted":
        fused = _fuse_weighted(bm25_hits, sem_hits, alpha=alpha)
    elif mode == "rrf":
        fused = _fuse_rrf(bm25_hits, sem_hits, rrf_k=rrf_k)
    else:
        # 兜底：未知 mode 当 weighted 处理，避免上层因拼写错崩溃。
        logger.warning(f"未知 hybrid mode={mode}，回退到 weighted")
        fused = _fuse_weighted(bm25_hits, sem_hits, alpha=alpha)

    out: list[tuple[Document, float, dict]] = []
    for cid, doc, meta, score, debug in fused[:final_top_k]:
        out.append((Document(page_content=doc, metadata=meta), score, debug))
    return out
