from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services import knowledge_service
from app.agents.services.embedding import embed_one
from mcp_server.auth import CallContext
from mcp_server.registry import tool


@tool(
    name="search_knowledge_base",
    description=(
        "Semantic search across the calling agent's uploaded documents "
        "(PDF, MD, TXT). Returns the top matching text chunks with their "
        "similarity score. Use this when the user asks about something the "
        "agent has been given knowledge about (tax laws, accounting rules, "
        "personal notes, etc.)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "top_n": {"type": "integer", "minimum": 1, "maximum": 25, "default": 6},
            "similarity_threshold": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.0,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    tags=["read", "knowledge"],
)
async def search_knowledge_base(
    *,
    session: AsyncSession,
    ctx: CallContext,
    query: str,
    top_n: int = 6,
    similarity_threshold: float = 0.0,
) -> dict[str, Any]:
    if not ctx.agent_id:
        return {"error": "agent_id required (this tool only works inside an agent conversation)"}
    vec = await embed_one(query)
    if vec is None:
        return {"items": [], "total": 0, "warning": "embedding failed"}
    hits = await knowledge_service.similarity_search(
        session,
        agent_id=ctx.agent_id,
        query_embedding=vec,
        top_n=int(top_n),
        similarity_threshold=float(similarity_threshold),
    )
    return {"items": hits, "total": len(hits)}
