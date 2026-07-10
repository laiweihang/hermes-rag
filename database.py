"""向量数据库（ChromaDB）操作层。

定位：本模块封装所有与 Chroma 的交互，对外提供：

- 嵌入客户端动态构造（``get_embeddings`` + 缓存）
- 向量库 CRUD（``add / get / delete / query``）
- 文档级聚合（按 source 列出/统计/删除）

集合命名约定：
    全项目只用一个 collection 名 ``"langchain"`` —— 这是 LangChain Chroma
    封装在 ``add_documents`` 时使用的默认名，保留原名兼容旧数据。

距离语义：
    Chroma 默认使用 L2/欧氏距离，越小越相似。``search_chunks`` 直接
    透传该距离值；上层 ``rag_engine`` 自行换算成相关度阈值。
"""

import hashlib
import logging
import os

# 关闭 ChromaDB 匿名遥测：避免冷启动联网拖慢首次响应；必须在 import
# chromadb 之前设置才生效。
os.environ.setdefault('ANONYMIZED_TELEMETRY', 'False')

import chromadb
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings

from config import DB_DIR

logger = logging.getLogger(__name__)


# ==========================================
# 嵌入模型（动态按数据库中的 embedding Provider 构造）
# ==========================================

# 缓存键带 model_name 与 base_url：当管理员替换 Provider 后能自然失效，
# 不需要遍历清理。
_embedding_cache: dict[tuple, OpenAIEmbeddings] = {}


def _get_embedding_provider():
    """从数据库读取 model_type='embedding' 的活跃 Provider，优先默认。

    与 ocr_engine._get_ocr_provider 同样的两段式回退逻辑：先 default，
    再任意 active。
    """
    # lazy import：models 依赖 config，本模块也依赖 config，
    # 顶层 import 易触发循环。
    from models import LlmProvider, SessionLocal

    db = SessionLocal()
    try:
        provider = (
            db.query(LlmProvider)
            .filter(
                LlmProvider.model_type == "embedding",
                LlmProvider.is_active == True,
                LlmProvider.is_default == True,
            )
            .first()
        )
        if not provider:
            provider = (
                db.query(LlmProvider)
                .filter(
                    LlmProvider.model_type == "embedding",
                    LlmProvider.is_active == True,
                )
                .first()
            )
        return provider
    finally:
        db.close()


def get_embeddings() -> OpenAIEmbeddings:
    """根据数据库中的嵌入 Provider 动态构造 OpenAIEmbeddings 客户端。

    缺失或 api_key 为空时抛出明确的 RuntimeError，提示用户去管理后台配置。

    ``check_embedding_ctx_length=False`` 是关键：LangChain 默认会调
    ``tiktoken`` 校验文本是否超过 OpenAI 模型上下文，但本项目可能接入
    国产 BGE/智谱嵌入，分词器不一致会误报。关闭后由 Provider 自身处理
    超长截断。
    """
    provider = _get_embedding_provider()
    if not provider:
        raise RuntimeError(
            "未配置嵌入模型 Provider。请在管理后台 → 模型管理 中新增"
            " model_type='embedding' 的远程 API（如 OpenAI / 智谱 / SiliconFlow）。"
        )
    if not (provider.api_key or "").strip():
        raise RuntimeError(
            f"嵌入 Provider「{provider.name}」尚未配置 API Key，"
            f"请在管理后台 → 模型管理 中编辑并填写。"
        )

    cache_key = (provider.id, provider.model_name, provider.base_url)
    cached = _embedding_cache.get(cache_key)
    if cached is not None:
        return cached

    embeddings = OpenAIEmbeddings(
        base_url=provider.base_url,
        api_key=provider.api_key,
        model=provider.model_name,
        timeout=provider.timeout_seconds,
        check_embedding_ctx_length=False,
    )
    _embedding_cache[cache_key] = embeddings
    logger.info(
        f"🔢 创建嵌入客户端: {provider.name} ({provider.model_name} @ {provider.base_url})"
    )
    return embeddings


def invalidate_embedding_cache():
    """清空嵌入客户端缓存（在管理后台修改 embedding Provider 后调用）。"""
    _embedding_cache.clear()


# ==========================================
# ChromaDB 客户端
# ==========================================

# 模块级单例：PersistentClient 内部维护 SQLite 文件锁，多次构造会拖慢
# 启动并产生 "another instance is using the database" 警告。
_chroma_client = chromadb.PersistentClient(path=DB_DIR)


def get_vector_store():
    """获取 LangChain 风格的向量库（懒加载嵌入客户端）。

    每次调用都重新走 get_embeddings() 是为了让 Provider 切换实时生效；
    嵌入客户端本身有缓存，几乎无开销。
    """
    return Chroma(
        client=_chroma_client,
        embedding_function=get_embeddings(),
    )


def get_collection():
    """获取 chromadb 原生 collection 引用，避免重复 get_or_create。

    走原生 API 比 LangChain 包装更快，适合做"统计/列表/删除"等不需要
    嵌入的元数据操作（不会触发任何嵌入接口调用）。
    """
    return _chroma_client.get_or_create_collection("langchain")


def _stable_chunk_id(doc, ordinal: int) -> str:
    """为切片生成可复现的 ID，供 BM25 与向量通道共同使用。"""
    meta = doc.metadata or {}
    source = str(meta.get("source", ""))
    page = str(meta.get("page", ""))
    payload = f"{source}\0{page}\0{ordinal}\0{doc.page_content or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sanitize_chroma_metadata(metadata: dict) -> dict:
    """Keep Chroma-compatible scalar metadata and stringify complex values."""
    cleaned = {}
    for key, value in (metadata or {}).items():
        if value is None:
            continue
        cleaned[str(key)] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return cleaned


def add_documents_with_stable_ids(vector_store, docs) -> list[str]:
    """写入切片并显式指定稳定 ID，同时把 ID 保存到 metadata。"""
    ids: list[str] = []
    source_ordinals: dict[str, int] = {}
    for doc in docs:
        source = str((doc.metadata or {}).get("source", ""))
        ordinal = source_ordinals.get(source, 0)
        source_ordinals[source] = ordinal + 1
        chunk_id = _stable_chunk_id(doc, ordinal)
        doc.metadata = dict(doc.metadata or {})
        doc.metadata["_id"] = chunk_id
        doc.metadata["chunk_id"] = chunk_id
        ids.append(chunk_id)
    if ids:
        vector_store.add_documents(docs, ids=ids)
    return ids


def replace_documents_by_source_atomic(source_name: str, docs) -> int:
    """原子式替换单个来源的切片。

    所有文本先完成嵌入；随后 upsert 新切片；只有新切片写入成功后才删除
    已不再存在的旧 ID。解析或嵌入失败不会影响旧索引。
    """
    docs = list(docs)
    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict] = []
    for ordinal, doc in enumerate(docs):
        chunk_id = _stable_chunk_id(doc, ordinal)
        meta = _sanitize_chroma_metadata(doc.metadata or {})
        meta["source"] = source_name
        meta["_id"] = chunk_id
        meta["chunk_id"] = chunk_id
        ids.append(chunk_id)
        texts.append(doc.page_content or "")
        metadatas.append(meta)

    if not ids:
        raise ValueError("文档未产生任何有效切片")

    embeddings = get_embeddings().embed_documents(texts)
    col = get_collection()
    old = col.get(where={"source": source_name}, include=[])
    old_ids = set(old.get("ids") or [])

    col.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    obsolete_ids = sorted(old_ids - set(ids))
    if obsolete_ids:
        col.delete(ids=obsolete_ids)
    return len(ids)


def document_count() -> int:
    """返回向量库中的文档片段总数（轻量级，不读元数据）。"""
    return get_collection().count()


def list_document_sources() -> dict:
    """返回 ``{文件名: 片段数}`` 字典。

    实现说明：Chroma 不支持服务端 GROUP BY，只能拉所有 metadata 到内存
    再聚合。当向量数 > 数十万时会偏慢，但管理后台是低频操作，可接受。
    """
    col = get_collection()
    total = col.count()
    if total == 0:
        return {}
    # limit=total 一次性拉全部 metadata；不带 documents/embeddings 字段
    # 以减少传输量。
    result = col.get(include=["metadatas"], limit=total)
    counts: dict[str, int] = {}
    for meta in (result.get("metadatas") or []):
        if meta is None:
            continue
        src = meta.get("source", "未知文件")
        counts[src] = counts.get(src, 0) + 1
    return counts


def list_ingested_filenames() -> set[str]:
    """返回已入库的所有 source 文件名集合（用于跳过重复入库）。"""
    col = get_collection()
    total = col.count()
    if total == 0:
        return set()
    result = col.get(include=["metadatas"], limit=total)
    names = set()
    for meta in (result.get("metadatas") or []):
        if meta and "source" in meta:
            names.add(meta["source"])
    return names


def delete_documents_by_source(source_name: str) -> int:
    """按 source 元数据删除某份文档的所有片段，返回删除数量。

    Chroma 不支持 ``delete(where=...)``（旧版本），所以先 get 出 ids 再
    按 ids 删；这种两步法对 0 命中也不会报错。
    """
    col = get_collection()
    result = col.get(where={"source": source_name}, include=[])
    ids = result.get("ids") or []
    if ids:
        col.delete(ids=ids)
    return len(ids)


def clear_vector_store():
    """清空整个向量数据库（删除并重建 collection）。

    delete_collection 在 collection 不存在时会抛 ValueError，这里 swallow
    保证幂等。
    """
    try:
        _chroma_client.delete_collection("langchain")
    except ValueError:
        pass


def list_chunks(source_name: str = None, offset: int = 0, limit: int = 20) -> dict:
    """分页列出向量片段。如果指定 source_name 则按来源过滤。

    返回 ``{"chunks": [...], "total": N}``：total 是过滤后的总数（前端
    用于分页控件）。整体异常返回空结构而非抛错，避免管理页崩溃。
    """
    col = get_collection()
    try:
        if source_name:
            # 过滤分支需要先算 total（不带 limit 的 get），再分页取数据。
            all_result = col.get(where={"source": source_name}, include=[])
            total = len(all_result.get("ids") or [])
            result = col.get(
                where={"source": source_name},
                include=["documents", "metadatas"],
                offset=offset,
                limit=limit,
            )
        else:
            total = col.count()
            result = col.get(
                include=["documents", "metadatas"],
                offset=offset,
                limit=limit,
            )
    except Exception:
        return {"chunks": [], "total": 0}

    ids = result.get("ids") or []
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []

    # 三个并行数组按下标对齐拼成 dict 列表，前端 JSON 序列化更友好。
    chunks = []
    for i, cid in enumerate(ids):
        chunks.append({
            "id": cid,
            "content": docs[i] if i < len(docs) else "",
            "metadata": metas[i] if i < len(metas) else {},
        })
    return {"chunks": chunks, "total": total}


def get_chunk_by_id(chunk_id: str):
    """根据 ID 获取单个片段。返回 ``{"id", "content", "metadata"}`` 或 None。"""
    col = get_collection()
    try:
        result = col.get(ids=[chunk_id], include=["documents", "metadatas"])
    except Exception:
        return None
    ids = result.get("ids") or []
    if not ids:
        return None
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []
    return {
        "id": ids[0],
        "content": docs[0] if docs else "",
        "metadata": metas[0] if metas else {},
    }


def update_chunk_content(chunk_id: str, new_content: str) -> bool:
    """更新片段内容（重新嵌入）。

    先生成新向量，再调用 Chroma update 原地替换，避免嵌入失败时丢失旧片段，
    并保证 chunk ID 在编辑前后保持不变。
    """
    col = get_collection()
    try:
        result = col.get(ids=[chunk_id], include=["metadatas"])
    except Exception:
        return False
    ids = result.get("ids") or []
    if not ids:
        return False
    meta = dict((result.get("metadatas") or [{}])[0] or {})
    meta["_id"] = chunk_id
    meta["chunk_id"] = chunk_id
    try:
        embedding = get_embeddings().embed_documents([new_content])[0]
        col.update(
            ids=[chunk_id],
            embeddings=[embedding],
            documents=[new_content],
            metadatas=[meta],
        )
        return True
    except Exception:
        return False


def delete_chunk_by_id(chunk_id: str) -> bool:
    """根据 ID 删除单个片段。

    先 get 一次校验存在性，避免 Chroma 在缺失 ID 上删除时返回成功
    （静默失败会让前端误以为操作生效）。
    """
    col = get_collection()
    try:
        existing = col.get(ids=[chunk_id], include=[])
        if not (existing.get("ids") or []):
            return False
        col.delete(ids=[chunk_id])
        return True
    except Exception:
        return False


def search_chunks(query_text: str, top_k: int = 10, source_name: str = None) -> list:
    """按相似度搜索片段。返回 ``[{"id", "content", "metadata", "distance"}]``。

    供 ``/api/chunks/search`` 用于前端"向量片段"页的语义检索；
    RAG 生成路径走的是 LangChain Chroma 的 similarity_search_with_score，
    距离归一化方式可能不同。
    """
    col = get_collection()
    if col.count() == 0:
        return []
    try:
        # 走 OpenAIEmbeddings 把 query 文本转向量再调原生 query —— 比
        # LangChain similarity_search 少一层包装开销。
        query_embedding = get_embeddings().embed_query(query_text)
        kwargs = {
            "query_embeddings": [query_embedding],
            # 防御 top_k > 库内总数 的边界情况（Chroma 会报错）。
            "n_results": min(top_k, col.count()),
            "include": ["documents", "metadatas", "distances"],
        }
        if source_name:
            kwargs["where"] = {"source": source_name}
        result = col.query(**kwargs)
    except Exception as e:
        logger.error(f"❌ 向量搜索失败: {e}")
        return []

    # query 接口返回的是嵌套 list（多 query 支持），单 query 取 [0]。
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    chunks = []
    for i, cid in enumerate(ids):
        chunks.append({
            "id": cid,
            "content": docs[i] if i < len(docs) else "",
            "metadata": metas[i] if i < len(metas) else {},
            "distance": dists[i] if i < len(dists) else 0.0,
        })
    return chunks
