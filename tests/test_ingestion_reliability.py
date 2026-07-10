from langchain_core.documents import Document
import pytest


def test_atomic_replace_does_not_delete_old_chunks_when_embedding_fails(monkeypatch):
    import database

    calls = []

    class BrokenEmbeddings:
        def embed_documents(self, texts):
            raise RuntimeError("embedding down")

    class FakeCollection:
        def get(self, **kwargs):
            calls.append(("get", kwargs))
            return {"ids": ["old-id"]}

        def upsert(self, **kwargs):
            calls.append(("upsert", kwargs))

        def delete(self, **kwargs):
            calls.append(("delete", kwargs))

    monkeypatch.setattr(database, "get_embeddings", lambda: BrokenEmbeddings())
    monkeypatch.setattr(database, "get_collection", lambda: FakeCollection())

    with pytest.raises(RuntimeError, match="embedding down"):
        database.replace_documents_by_source_atomic(
            "a.md", [Document(page_content="new", metadata={"source": "a.md"})]
        )

    assert calls == []


def test_atomic_replace_upserts_before_deleting_obsolete_chunks(monkeypatch):
    import database

    calls = []

    class FakeEmbeddings:
        def embed_documents(self, texts):
            return [[0.1, 0.2] for _ in texts]

    class FakeCollection:
        def get(self, **kwargs):
            calls.append("get")
            return {"ids": ["old-id"]}

        def upsert(self, **kwargs):
            calls.append("upsert")

        def delete(self, **kwargs):
            calls.append("delete")

    monkeypatch.setattr(database, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(database, "get_collection", lambda: FakeCollection())

    count = database.replace_documents_by_source_atomic(
        "a.md", [Document(page_content="new", metadata={"source": "a.md"})]
    )

    assert count == 1
    assert calls == ["get", "upsert", "delete"]


def test_atomic_replace_sanitizes_complex_metadata(monkeypatch):
    import database

    captured = {}

    class FakeEmbeddings:
        def embed_documents(self, texts):
            return [[0.1, 0.2] for _ in texts]

    class FakeCollection:
        def get(self, **kwargs):
            return {"ids": []}

        def upsert(self, **kwargs):
            captured.update(kwargs)

        def delete(self, **kwargs):
            raise AssertionError("nothing should be deleted")

    monkeypatch.setattr(database, "get_embeddings", lambda: FakeEmbeddings())
    monkeypatch.setattr(database, "get_collection", lambda: FakeCollection())

    database.replace_documents_by_source_atomic(
        "a.md",
        [Document(
            page_content="new",
            metadata={"source": "a.md", "nullable": None, "tags": ["a", "b"]},
        )],
    )

    metadata = captured["metadatas"][0]
    assert "nullable" not in metadata
    assert metadata["tags"] == "['a', 'b']"
