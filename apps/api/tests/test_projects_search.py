"""Projects and full-text search."""

from __future__ import annotations

from httpx import AsyncClient
from tests.test_files_api import upload

from kimi.routers.search import build_match_query

# ---- projects -------------------------------------------------------------


async def test_project_crud(client: AsyncClient) -> None:
    created = await client.post(
        "/api/projects", json={"name": "Libya monitoring", "instructions": "Be precise."}
    )
    assert created.status_code == 201
    pid = created.json()["id"]

    listed = (await client.get("/api/projects")).json()
    assert [p["id"] for p in listed["projects"]] == [pid]

    patched = await client.patch(f"/api/projects/{pid}", json={"name": "Renamed"})
    assert patched.json()["name"] == "Renamed"

    assert (await client.delete(f"/api/projects/{pid}")).status_code == 204
    assert (await client.get(f"/api/projects/{pid}")).status_code == 404


async def test_project_lists_its_conversations(client: AsyncClient) -> None:
    pid = (await client.post("/api/projects", json={"name": "P"})).json()["id"]
    convo = (
        await client.post("/api/conversations", json={"title": "In project", "project_id": pid})
    ).json()

    detail = (await client.get(f"/api/projects/{pid}")).json()
    assert detail["conversation_count"] == 1
    assert detail["conversations"][0]["id"] == convo["id"]


async def test_deleting_a_project_deletes_its_conversations_and_files(
    client: AsyncClient,
) -> None:
    """Data retention: deleting a project must remove everything inside it."""
    pid = (await client.post("/api/projects", json={"name": "P"})).json()["id"]
    convo = (await client.post("/api/conversations", json={"title": "C", "project_id": pid})).json()
    body = await upload(client, convo["id"], [("a.txt", b"secret contents here", "text/plain")])
    file_id = body["files"][0]["id"]

    assert (await client.delete(f"/api/projects/{pid}")).status_code == 204
    assert (await client.get(f"/api/conversations/{convo['id']}")).status_code == 404
    assert (await client.get(f"/api/files/{file_id}")).status_code == 404


# ---- search query safety --------------------------------------------------


def test_match_query_quotes_every_term() -> None:
    assert build_match_query("libya oil") == '"libya" AND "oil"*'


def test_fts_operators_in_user_text_are_neutralised() -> None:
    """A stray quote or operator must not become FTS5 syntax."""
    for hostile in ('libya" OR 1=1 --', "NEAR(a b)", "libya AND NOT oil", '"'):
        built = build_match_query(hostile)
        # Every term is quoted; nothing survives as a bare operator.
        assert built.count('"') % 2 == 0
        assert " OR " not in built


def test_arabic_terms_survive() -> None:
    assert '"ليبيا"' in build_match_query("أخبار ليبيا")


def test_empty_query_yields_nothing() -> None:
    assert build_match_query("   !!!  ") == ""


# ---- search behaviour -----------------------------------------------------


async def test_search_finds_message_text(client: AsyncClient, conversation_id: str) -> None:
    await client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "content": "Sharara oilfield output"},
    )
    body = (await client.get("/api/search", params={"q": "Sharara"})).json()
    assert body["available"] is True
    assert any(
        "Sharara" in r["excerpt"] or "Sharara" in (r["title"] or "") for r in body["results"]
    )


async def test_search_finds_text_inside_an_uploaded_document(
    client: AsyncClient, conversation_id: str
) -> None:
    """A document must be findable by its contents, not only its filename."""
    await upload(
        client,
        conversation_id,
        [("report.txt", b"Benghazi refinery throughput doubled", "text/plain")],
    )
    body = (await client.get("/api/search", params={"q": "Benghazi", "scope": "files"})).json()
    assert body["results"], body
    assert body["results"][0]["kind"] == "file"


async def test_search_can_be_scoped_to_one_conversation(
    client: AsyncClient, conversation_id: str
) -> None:
    other = (await client.post("/api/conversations", json={"title": "Other"})).json()["id"]
    await client.post(
        "/api/chat/stream", json={"conversation_id": conversation_id, "content": "alpha marker"}
    )
    await client.post(
        "/api/chat/stream", json={"conversation_id": other, "content": "alpha marker"}
    )

    scoped = (
        await client.get("/api/search", params={"q": "alpha", "conversation_id": conversation_id})
    ).json()
    assert scoped["results"]
    assert all(r["conversation_id"] == conversation_id for r in scoped["results"])


async def test_deleted_content_leaves_the_index(client: AsyncClient, conversation_id: str) -> None:
    """Triggers keep the index in sync, so deletes really remove findability."""
    await client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "content": "ephemeral watchword"},
    )
    assert (await client.get("/api/search", params={"q": "ephemeral"})).json()["results"]

    await client.delete(f"/api/conversations/{conversation_id}")
    after = (await client.get("/api/search", params={"q": "ephemeral"})).json()
    assert after["results"] == []


async def test_hostile_query_returns_empty_not_an_error(client: AsyncClient) -> None:
    resp = await client.get("/api/search", params={"q": '"; DROP TABLE messages; --'})
    assert resp.status_code == 200
    # The table must still be there.
    assert (await client.get("/api/conversations")).status_code == 200
