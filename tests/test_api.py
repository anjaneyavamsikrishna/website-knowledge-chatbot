from fastapi.testclient import TestClient

from app.main import create_app


def client(container):
    return TestClient(create_app(container))


def test_health_and_sources(container):
    c = client(container)
    h = c.get("/api/health").json()
    assert h["status"] == "ok" and h["knowledge_base_ready"] is False
    srcs = c.get("/api/sources").json()
    assert len(srcs) == 3 and all(s["status"] == "never" for s in srcs)


def test_admin_endpoints_require_token(container):
    c = client(container)
    assert c.post("/api/ingest").status_code == 401
    assert c.post("/api/ingest", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert c.get("/api/logs/unanswered").status_code == 401
    ok = c.post("/api/ingest", headers={"X-Admin-Token": "secret-admin"})
    assert ok.status_code == 200 and ok.json()["updated"] == 3
    assert c.get("/api/health").json()["knowledge_base_ready"] is True
    assert all(s["status"] == "ok" for s in c.get("/api/sources").json())


def test_admin_disabled_when_no_token_configured(container):
    container.settings.admin_token = ""
    assert client(container).post("/api/ingest", headers={"X-Admin-Token": "x"}).status_code == 503


def test_chat_endpoint_and_validation(ingested, llm):
    c = client(ingested)
    llm.analysis.update(intent="package_details", standalone_question="package inclusions airport transfers")
    llm.generation = {"answerable": True, "answer": "Includes airport transfers.", "used_sources": ["S1"]}
    r = c.post("/api/chat", json={"question": "What is included in the package?"})
    assert r.status_code == 200
    body = r.json()
    assert body["answered"] and body["sources"] and body["session_id"]
    assert c.post("/api/chat", json={"question": ""}).status_code == 422
    assert c.post("/api/chat", json={"question": "hi", "session_id": "bad id!"}).status_code == 422


def test_chat_on_empty_kb_returns_503(container, llm):
    llm.analysis.update(intent="general_info")
    r = client(container).post("/api/chat", json={"question": "adrenal package price"})
    assert r.status_code == 503


def test_unanswered_log_endpoint(ingested, llm):
    c = client(ingested)
    llm.analysis.update(intent="general_info", standalone_question="weather forecast sunshine beach")
    c.post("/api/chat", json={"question": "weather forecast sunshine beach"})
    r = c.get("/api/logs/unanswered", headers={"X-Admin-Token": "secret-admin"})
    assert r.status_code == 200 and r.json()["items"][0]["fallback_reason"] == "no_relevant_content"


def test_lead_requires_consent_and_valid_email(container):
    c = client(container)
    base = {"name": "Ana Perez", "email": "ana@example.com", "consent": True}
    assert c.post("/api/leads", json={**base, "consent": False}).status_code == 400
    assert c.post("/api/leads", json={**base, "email": "nope"}).status_code == 422
    ok = c.post("/api/leads", json=base)
    assert ok.status_code == 200 and "does NOT submit a quote request" in ok.json()["message"]
    leads = c.get("/api/logs/leads", headers={"X-Admin-Token": "secret-admin"}).json()
    assert leads["count"] == 1


def test_rate_limit(container):
    from app.api.deps import RateLimiter
    app = create_app(container)
    app.state.rate_limiter = RateLimiter(2)
    c = TestClient(app)
    codes = [c.post("/api/leads", json={"name": "Ana", "email": "a@b.co", "consent": True}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_frontend_served(container):
    c = client(container)
    assert c.get("/").status_code == 200 and "PlacidWay Assistant" in c.get("/").text
    assert c.get("/static/script.js").status_code == 200
