"""Integration tests. Require a reachable Postgres via DATABASE_URL (CI service container or compose)."""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200


def test_create_and_list(client):
    r = client.post("/api/ideas", json={"content": "ship it"})
    assert r.status_code == 201
    created = r.json()
    assert created["content"] == "ship it" and created["id"] > 0

    ideas = client.get("/api/ideas").json()
    assert any(i["id"] == created["id"] for i in ideas)


@pytest.mark.parametrize("payload", [{}, {"content": ""}, {"content": "x" * 1001}])
def test_validation(client, payload):
    assert client.post("/api/ideas", json=payload).status_code == 422
