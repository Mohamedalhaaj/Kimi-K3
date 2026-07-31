from __future__ import annotations

from httpx import AsyncClient


async def test_create_and_fetch(client: AsyncClient) -> None:
    created = await client.post("/api/conversations", json={"title": "Libya news"})
    assert created.status_code == 201
    cid = created.json()["id"]

    got = await client.get(f"/api/conversations/{cid}")
    assert got.status_code == 200
    assert got.json()["title"] == "Libya news"
    assert got.json()["messages"] == []


async def test_rename_is_deterministic_and_immediate(
    client: AsyncClient, conversation_id: str, provider
) -> None:
    resp = await client.patch(f"/api/conversations/{conversation_id}", json={"title": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed"
    # The brief requires deterministic actions to never consult the model.
    assert provider.calls == []


async def test_missing_conversation_returns_typed_error(client: AsyncClient) -> None:
    resp = await client.get("/api/conversations/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_pin_and_ordering(client: AsyncClient) -> None:
    a = (await client.post("/api/conversations", json={"title": "A"})).json()
    b = (await client.post("/api/conversations", json={"title": "B"})).json()
    await client.patch(f"/api/conversations/{a['id']}", json={"pinned": True})

    listing = (await client.get("/api/conversations")).json()
    assert listing["items"][0]["id"] == a["id"]
    assert listing["page"]["total"] == 2
    assert {i["id"] for i in listing["items"]} == {a["id"], b["id"]}


async def test_title_search_uses_bound_parameter(client: AsyncClient) -> None:
    await client.post("/api/conversations", json={"title": "Tariff research"})
    await client.post("/api/conversations", json={"title": "Holiday plans"})

    hit = (await client.get("/api/conversations", params={"q": "tariff"})).json()
    assert [i["title"] for i in hit["items"]] == ["Tariff research"]

    # A SQL metacharacter must be treated as data, not syntax.
    injected = (
        await client.get("/api/conversations", params={"q": "'; DROP TABLE messages;--"})
    ).json()
    assert injected["items"] == []
    assert (await client.get("/api/conversations")).json()["page"]["total"] == 2


async def test_delete_cascades_to_messages(client: AsyncClient, conversation_id: str) -> None:
    await client.post(
        "/api/chat/stream", json={"conversation_id": conversation_id, "content": "hi"}
    )
    detail = (await client.get(f"/api/conversations/{conversation_id}")).json()
    assert len(detail["messages"]) == 2

    assert (await client.delete(f"/api/conversations/{conversation_id}")).status_code == 204
    assert (await client.get(f"/api/conversations/{conversation_id}")).status_code == 404


async def test_clear_messages_keeps_conversation(client: AsyncClient, conversation_id: str) -> None:
    await client.post(
        "/api/chat/stream", json={"conversation_id": conversation_id, "content": "hi"}
    )
    assert (
        await client.delete(f"/api/conversations/{conversation_id}/messages")
    ).status_code == 204

    detail = (await client.get(f"/api/conversations/{conversation_id}")).json()
    assert detail["messages"] == []
    assert detail["id"] == conversation_id
