from __future__ import annotations

from httpx import AsyncClient


async def test_healthz_does_not_touch_dependencies(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_reports_each_check(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["model_provider"]["ok"] is True


async def test_request_id_header_is_echoed(client: AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"


async def test_models_endpoint_advertises_capabilities(client: AsyncClient) -> None:
    resp = await client.get("/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default"]
    ids = {m["id"] for m in body["models"]}
    assert "moonshotai/kimi-k3-free" in ids
    for model in body["models"]:
        assert "text" in model["capabilities"]
        assert model["context_window"] > 0
