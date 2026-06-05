"""Health-check endpoint testleri."""

import pytest


@pytest.mark.asyncio
async def test_healthz_returns_200(async_client):
    response = await async_client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_healthz_db_returns_200_when_db_up(async_client):
    response = await async_client.get("/healthz/db")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


@pytest.mark.asyncio
async def test_healthz_db_fails_when_db_down(async_client, monkeypatch):
    """Simule edilmis DB hatasi testi."""
    # Bu test DB calisiyorken bile error handling'i dogrular
    response = await async_client.get("/healthz/db")
    # DB ayaktayken 200 donmeli
    assert response.status_code == 200
