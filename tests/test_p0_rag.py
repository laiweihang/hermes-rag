from langchain_core.documents import Document
from fastapi.testclient import TestClient


def test_stable_chunk_ids_are_reproducible_per_source():
    from database import add_documents_with_stable_ids

    class FakeVectorStore:
        def __init__(self):
            self.calls = []

        def add_documents(self, docs, ids):
            self.calls.append((docs, ids))

    docs1 = [
        Document(page_content="same", metadata={"source": "a.pdf", "page": 1}),
        Document(page_content="same", metadata={"source": "a.pdf", "page": 1}),
        Document(page_content="other", metadata={"source": "b.pdf", "page": 1}),
    ]
    docs2 = [
        Document(page_content="same", metadata={"source": "a.pdf", "page": 1}),
        Document(page_content="same", metadata={"source": "a.pdf", "page": 1}),
        Document(page_content="other", metadata={"source": "b.pdf", "page": 1}),
    ]

    ids1 = add_documents_with_stable_ids(FakeVectorStore(), docs1)
    ids2 = add_documents_with_stable_ids(FakeVectorStore(), docs2)

    assert ids1 == ids2
    assert len(set(ids1)) == 3
    assert docs1[0].metadata["_id"] == ids1[0]
    assert docs1[0].metadata["chunk_id"] == ids1[0]


def test_semantic_search_uses_real_chroma_ids(monkeypatch):
    import retrieval

    class FakeEmbeddings:
        def embed_query(self, query):
            return [0.1, 0.2]

    class FakeCollection:
        def count(self):
            return 1

        def query(self, **kwargs):
            return {
                "ids": [["chroma-id-1"]],
                "documents": [["content"]],
                "metadatas": [[{"source": "a.pdf"}]],
                "distances": [[0.1]],
            }

    monkeypatch.setattr(retrieval, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(retrieval, "get_collection", lambda: FakeCollection())

    hits = retrieval._semantic_search("question", top_k=5, threshold=0.0)

    assert hits[0][0] == "chroma-id-1"
    assert hits[0][2]["_id"] == "chroma-id-1"


def test_rerank_receives_recall_candidates_before_final_cut(monkeypatch):
    import rag_engine
    import reranker

    seen = {}

    def fake_hybrid_search(question, **kwargs):
        seen["hybrid_final_top_k"] = kwargs["final_top_k"]
        return [
            (Document(page_content=f"chunk {i}"), float(i), {})
            for i in range(kwargs["final_top_k"])
        ]

    def fake_rerank(question, hits, *, top_n, provider_id=None):
        seen["candidate_count"] = len(hits)
        seen["top_n"] = top_n
        return hits[:top_n]

    monkeypatch.setattr(rag_engine, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(reranker, "rerank", fake_rerank)

    result = rag_engine._retrieve_with_rerank(
        "question",
        {
            "mode": "weighted",
            "alpha": 0.5,
            "rrf_k": 60,
            "bm25_top_k": 20,
            "vector_top_k": 20,
            "final_top_k": 5,
            "semantic_threshold": 0.0,
            "enable_bm25": True,
            "rerank_enabled": True,
            "rerank_top_n": 5,
            "rerank_provider_id": None,
        },
    )

    assert seen == {"hybrid_final_top_k": 20, "candidate_count": 20, "top_n": 5}
    assert len(result) == 5


def test_shared_knowledge_base_is_admin_managed():
    from api import app
    from auth import create_access_token

    client = TestClient(app)
    token = create_access_token("regular-user", role="user")
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/documents", headers=headers).status_code == 403
    assert client.get("/api/chunks", headers=headers).status_code == 403

    status_response = client.get("/api/knowledge/status", headers=headers)
    assert status_response.status_code == 200
    assert set(status_response.json()) == {"documents", "chunks"}
