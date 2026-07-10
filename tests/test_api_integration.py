import uuid

from fastapi.testclient import TestClient


def _register(client: TestClient, role: str = "user") -> tuple[str, str]:
    from models import SessionLocal, User

    username = f"test-{uuid.uuid4().hex[:10]}"
    password = "secret123"
    response = client.post("/auth/register", json={"username": username, "password": password})
    assert response.status_code == 200
    if role == "admin":
        db = SessionLocal()
        user = db.query(User).filter(User.username == username).one()
        user.role = "admin"
        db.commit()
        db.close()
        response = client.post("/auth/login", json={"username": username, "password": password})
    return username, response.json()["access_token"]


def test_authenticated_conversation_round_trip(monkeypatch):
    import api

    client = TestClient(api.app)
    _, token = _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(
        api,
        "generate_answer",
        lambda *args, **kwargs: {
            "answer": "工作日为1.5倍。[1]",
            "sources": [{
                "index": 1,
                "source": "hr.md",
                "content": "工作日为1.5倍。",
                "score": 0.1,
                "chunk_id": "chunk-1",
                "page": 1,
            }],
            "rule_matched": None,
            "citation_validation": {"valid": True},
        },
    )

    created = client.post("/api/conversations", headers=headers, json={})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={"question": "工作日加班几倍？"},
    )
    assert sent.status_code == 200
    assistant = sent.json()["assistant_message"]
    assert assistant["sources"][0]["chunk_id"] == "chunk-1"
    assert assistant["citation_validation"]["valid"] is True

    detail = client.get(f"/api/conversations/{conversation_id}", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["messages"]) == 2


def test_document_management_requires_admin(monkeypatch):
    import api

    client = TestClient(api.app)
    _, user_token = _register(client)
    _, admin_token = _register(client, role="admin")

    user_headers = {"Authorization": f"Bearer {user_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    assert client.get("/api/documents", headers=user_headers).status_code == 403

    monkeypatch.setattr(api, "list_document_sources", lambda: {"demo.md": 3})
    response = client.get("/api/documents", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["documents"][0]["status"] == "ready"


def test_query_response_exposes_citation_validation(monkeypatch):
    import api

    client = TestClient(api.app)
    _, token = _register(client)
    monkeypatch.setattr(
        api,
        "generate_answer",
        lambda *args, **kwargs: {
            "answer": "答案。[1]",
            "sources": [{"index": 1, "source": "a.md", "content": "答案。", "score": 0.0}],
            "rule_matched": None,
            "citation_validation": {"valid": True, "invalid_indices": []},
        },
    )
    response = client.post(
        "/query",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "问题"},
    )
    assert response.status_code == 200
    assert response.json()["citation_validation"]["valid"] is True
