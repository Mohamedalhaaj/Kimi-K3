"""Search across conversations and uploaded documents.

Backed by SQLite FTS5, kept in sync by database triggers rather than
application code — a write that bypasses the ORM cannot leave the index stale.

The user's query is never interpolated into SQL. FTS5 has its own query syntax
in which a stray quote or a bare ``AND`` is a syntax error, so terms are
extracted and re-quoted before they reach the engine.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import bindparam, text

from kimi.deps import SessionDep

router = APIRouter(prefix="/search", tags=["search"])

Scope = Literal["all", "messages", "files"]

#: Word characters in any script, so Arabic terms survive.
_TERM = re.compile(r"[^\W_]+", re.UNICODE)


def build_match_query(raw: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Every term is double-quoted, which makes FTS5 treat it as a literal and
    removes the possibility of the user's text being read as operators.
    """
    terms = _TERM.findall(raw or "")
    if not terms:
        return ""
    # Prefix-match the final term so search feels responsive while typing.
    quoted = [f'"{t}"' for t in terms[:-1]]
    quoted.append(f'"{terms[-1]}"*')
    return " AND ".join(quoted)


@router.get("")
async def search(
    session: SessionDep,
    q: str = Query(min_length=1, max_length=200),
    scope: Scope = "all",
    limit: int = Query(20, ge=1, le=100),
    conversation_id: str | None = None,
) -> dict[str, Any]:
    match = build_match_query(q)
    if not match:
        return {"query": q, "results": [], "total": 0}

    where = ["search_index MATCH :match"]
    params: dict[str, Any] = {"match": match, "limit": limit}
    if scope != "all":
        where.append("kind = :kind")
        params["kind"] = "message" if scope == "messages" else "file"
    if conversation_id:
        where.append("conversation_id = :conversation_id")
        params["conversation_id"] = conversation_id

    statement = text(
        f"""
        SELECT
            s.kind,
            s.ref_id,
            s.conversation_id,
            s.title,
            snippet(search_index, 4, '[', ']', ' … ', 12) AS excerpt,
            bm25(search_index) AS score,
            c.title AS conversation_title
        FROM search_index s
        LEFT JOIN conversations c ON c.id = s.conversation_id
        WHERE {" AND ".join(where)}
        ORDER BY score
        LIMIT :limit
        """  # noqa: S608 - `where` is built from a fixed allowlist, never user text
    ).bindparams(bindparam("match"))

    try:
        rows = (await session.execute(statement, params)).mappings().all()
    except Exception:
        return {"query": q, "results": [], "total": 0, "available": False}

    return {
        "query": q,
        "available": True,
        "total": len(rows),
        "results": [
            {
                "kind": row["kind"],
                "id": row["ref_id"],
                "conversation_id": row["conversation_id"],
                "conversation_title": row["conversation_title"],
                "title": row["title"],
                "excerpt": row["excerpt"],
                # bm25 returns lower-is-better; expose it as-is rather than
                # inventing a normalised score we cannot justify.
                "rank": round(float(row["score"]), 4),
            }
            for row in rows
        ],
    }
